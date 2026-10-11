"""Behaviour checks for the LRGB workflow, using known mixed-scale lunar-like detail."""
import copy
import hashlib
import json
from pathlib import Path
import shutil
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import cv2
import numpy as np
import tifffile
import chain
from project import Project, DEFAULT_EDITS, edits_to_chain
import lrgb
from lrgb import Image, manual_match, largest_rectangle
import app as app_module
from app import Application, start_server
from alignment import measure
from recent import RecentProjects
import orient
from atlas_geo import Geometry, NoUsableLimb, similarity as atlas_similarity
if __package__:
    from .synthetic import detail, lunar_disk
else:
    from synthetic import detail, lunar_disk


# The tests below are phrased in the old flat adjustments, which a schema-3 project migrates from (Appendix D):
# these helpers turn one into its chain, geometry and layer switches, and use the phase-7 API.
def doc_of(edits): return edits_to_chain(dict(DEFAULT_EDITS,**edits))[0]
def geo(edits): return {k:edits[k] for k in ('rotation','mirror','coverage')}
def layers(edits): return {k:edits[k] for k in ('l_visible','rgb_visible')}
def render(scene,m,size,edits,view='composite',precise=True): return scene.render(m,size,doc_of(edits),layers(edits),view,precise)
def export(scene,out,edits,factor=1,layer='composite',**kw): return scene.export(out,doc_of(edits),geo(edits),layers(edits),factor,layer,**kw)
def store(p,edits): p.set_chain(doc_of(edits));p.set_geometry(geo(edits));p.set_view(layers(edits))


class Workflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.l = detail()
        self.rgb = np.stack([self.l*1.08, self.l, self.l*.92], axis=-1)
        self.lp, self.cp = self.root/'L.tif', self.root/'RGB.tif'
        tifffile.imwrite(self.lp, np.round(self.l*65535).astype(np.uint16))
        tifffile.imwrite(self.cp, np.round(self.rgb*65535).astype(np.uint16), photometric='rgb')
        self.app = Application()
        self.app.select(self.root/'moon.lunarmosaic', True, 'Test Moon')
        self.app.inputs('L', [str(self.lp)])
        self.app.inputs('RGB', [str(self.cp)])

    def tearDown(self):
        self.app.close()
        self.temp.cleanup()

    def built(self):
        # colour is placed onto the luminance mosaic (phase 6): the colour image matches it 1:1 here
        self.assertEqual(self.app.build(['L', 'RGB']), {'L':'built', 'RGB':'built'})
        return self.app.get_scene()

    def legacy_colour(self, rgb):
        """A colour mosaic matched the schema-3 way (one global transform), as a project from before phase 6 has
        it: alignment.measure must still find its local errors."""
        p=self.app.project
        tifffile.imwrite(p.path('derived/RGB.tif'),np.uint16(np.clip(rgb,0,1)*65535),photometric='rgb')
        p.stream('colour').update(state='built',mosaic='derived/RGB.tif',placement=None,
            legacy_match=manual_match([[30,30],[550,30],[30,550],[550,550]],[[30,30],[550,30],[30,550],[550,550]]))
        p.save();self.app.scene=None
        return self.app.get_scene()

    def test_native_build_preserves_sources_and_reopens(self):
        before = [hashlib.sha256(x.read_bytes()).digest() for x in (self.lp,self.cp)]
        self.built()
        self.app.close()
        p = Project.open(self.root/'moon.lunarmosaic')
        self.assertEqual(p.stream('luminance')['state'], 'built')
        self.assertEqual(p.stream('colour')['width'], 640)
        self.assertIsNotNone(p.match)
        self.assertEqual(before, [hashlib.sha256(x.read_bytes()).digest() for x in (self.lp,self.cp)])

    def legacy(self,version,edits):
        """Rewrite the open project's file in its schema-1–3 shape (sets by key, a global match, flat edits)."""
        p=self.app.project;self.app.close()
        own=lambda key:{k:v for k,v in p.set(key).items() if k not in ('id','stream','label')}
        state=lambda name:{k:v for k,v in p.stream(name).items() if k not in ('legacy_match','placement')}
        sets={'L':dict(state('luminance'),**own('L')),'RGB':dict(state('colour'),**own('RGB'))}
        old=dict(version=version,id=p.data['id'],name=p.data['name'],created=p.data['created'],updated=p.data['updated'],
                 revision=p.data['revision'],sets=sets,match=p.match,orientation=None,edits=edits)
        (p.folder/'project.json').write_text(json.dumps(old),'utf-8')
        return p.folder,old

    def test_version_one_migrates_without_changing_existing_edits(self):
        self.built()
        old_keys=('saturation','smoothing','strength','highlights','contrast','rotation','coverage')
        folder,_=self.legacy(1,dict({k:DEFAULT_EDITS[k] for k in old_keys},saturation=2.3))
        restored=Project.open(folder)
        self.assertEqual(restored.data['version'],4)
        ch=restored.data['chain']['chains']
        self.assertEqual([(e['type'],e['params']['factor']) for e in ch['colour']],[('saturation',2.3)])
        self.assertEqual(ch['luminance'],[])                           # wavelets [0,0,0,0]: no block
        self.assertFalse(restored.data['geometry']['mirror'])
        self.assertEqual(restored.layers,dict(l_visible=True,rgb_visible=True))
        self.assertTrue((folder/'project.v1.json').is_file())

    def test_version_two_migrates_preserving_sharpening_and_match(self):
        self.built()
        edits={k:v for k,v in DEFAULT_EDITS.items() if k not in ('l_visible','rgb_visible')}
        folder,old=self.legacy(2,dict(edits,wavelets=[.1,.2,.3,.4]))
        self.app.select(folder)
        restored=self.app.project
        self.assertEqual(restored.data['version'],4)
        wave=restored.data['chain']['chains']['luminance'][0]
        self.assertEqual(wave['type'],'wavelets');np.testing.assert_allclose(wave['params']['biases'],[.01,.02,.03,.04])
        self.assertEqual(restored.match,old['match'])
        self.assertTrue(restored.layers['l_visible'])

    def test_visibility_reopens_and_combined_export_uses_visible_layer(self):
        scene=self.built();p=self.app.project
        matrix,size,_=scene.geometry(DEFAULT_EDITS)
        lonly=dict(DEFAULT_EDITS,rgb_visible=False)
        combined,_=render(scene,matrix,size,lonly)
        expected,_=render(scene,matrix,size,DEFAULT_EDITS,'luminance')
        self.assertLess(np.max(np.abs(combined-expected)),1e-6)
        conly=dict(DEFAULT_EDITS,l_visible=False)
        output=self.root/'visible-colour.tif';export(scene,output,conly)
        expected,_=render(scene,matrix,size,DEFAULT_EDITS,'colour')
        self.assertLess(np.max(np.abs(tifffile.imread(output).astype(np.float32)/65535-expected)),.00002)
        empty=dict(DEFAULT_EDITS,l_visible=False,rgb_visible=False)
        preview,_=render(scene,matrix,size,empty)
        self.assertEqual(float(preview.max()),0)
        with self.assertRaises(ValueError):self.app.export_options(geo(empty),layers(empty),dict(layer='composite'))
        self.app.export_options(geo(empty),layers(empty),dict(layer='luminance'))
        store(p,conly);p.save();self.app.close();self.app.select(p.folder)
        self.assertFalse(self.app.project.layers['l_visible'])
        self.assertTrue(self.app.project.layers['rgb_visible'])

    def test_independent_checks_detect_local_error_and_unknown_regions(self):
        shifted=cv2.warpAffine(self.rgb,np.array([[1,0,3],[0,1,0]],np.float32),(640,640))
        rgb=self.rgb.copy();rgb[:,320:]=shifted[:,320:]
        self.assertEqual(self.app.build(['L']),{'L':'built'})
        scene=self.legacy_colour(rgb)
        checks=measure(scene)
        left=[s['residual'] for s in checks['samples'] if s['state']=='measured' and s['x']<290]
        right=[s['residual'] for s in checks['samples'] if s['state']=='measured' and s['x']>360]
        self.assertGreater(len(left),10);self.assertGreater(len(right),10)
        self.assertLess(float(np.median(left)),.3)
        self.assertGreater(float(np.median(right)),2.5)
        self.assertLess(scene.match_meta['worst'],.001)  # manual point fit alone would miss the local error
        scene.match[0,2]=280
        checks=measure(scene)
        self.assertGreaterEqual(sum(s.get('reason')=='Insufficient shared coverage' for s in checks['samples']),12)

    def test_sharpened_native_export_has_no_tile_seams(self):
        scene=self.built()
        edits=dict(DEFAULT_EDITS,wavelets=[1.5,.5,0,0],wavelet_kernel='b3',usm_amount=.4,
                   usm_radius=1.3,usm_threshold=.005,rotation=19,mirror=True,smoothing=3)
        m,size,_=scene.geometry(edits,factor=.5)
        expected,_=render(scene,m,size,edits)
        out=self.root/'sharpened.tif';export(scene,out,edits,factor=.5)
        actual=Image(out).pixels(Image(out).raw)
        self.assertLess(float(np.mean(np.abs(actual-expected))),.0003)
        self.assertLess(float(np.max(np.abs(actual-expected))),.007)
        original,_=render(scene,m,size,dict(DEFAULT_EDITS,rotation=19,mirror=True))
        self.assertGreater(float(np.mean(np.abs(actual-original))),.001)
        # the sharpening blocks report what they did to the fine detail (/api/analysis)
        store(self.app.project,edits)
        readouts=self.app.analysis()['blocks']
        detail=[readouts[e['id']]['fine_detail'] for e in self.app.project.data['chain']['chains']['luminance'] if e['type'] in ('wavelets','unsharp_mask')]
        self.assertEqual(len(detail),2);self.assertTrue(all(v>1 for v in detail))

    def test_shared_mirror_keeps_all_pixel_centres(self):
        scene=self.built()
        m,size,_=scene.geometry(dict(DEFAULT_EDITS,mirror=True))
        self.assertEqual(size,(640,640))
        np.testing.assert_allclose(m@[0,0,1],[639,0,1])
        np.testing.assert_allclose(m@[639,639,1],[0,639,1])

    def test_north_up_transform_and_failure_preserve_sources(self):
        scene=self.built()
        for mirror in (False,True):
            a,t=atlas_similarity(320,320,200,37,mirror)
            geo=Geometry(0,0,a,t)
            with patch.object(orient,'locate',return_value=(geo,dict(matches=24,rms_px=.3))):
                result=orient.solve(self.app.project,log=lambda s:None)
            edits=dict(DEFAULT_EDITS,rotation=result['rotation'],mirror=result['mirror'])
            matrix,_,_=scene.geometry(edits)
            np.testing.assert_allclose(matrix[:2,:2],result['matrix'],atol=1e-12)
        before=copy.deepcopy(self.app.project.data['geometry'])
        with patch.object(orient,'locate',side_effect=NoUsableLimb('no limb')):
            with self.assertRaises(ValueError):orient.solve(self.app.project,log=lambda s:None)
        self.assertEqual(self.app.project.data['geometry'],before)

    def test_native_tiff_byte_order_planar_and_white_zero(self):
        pixels=np.round(self.l*65535).astype(np.uint16)
        big=self.root/'big-endian.tif'
        tifffile.imwrite(big,pixels,byteorder='>')
        im=Image(big)
        self.assertTrue(im.mapped)
        self.assertLess(float(np.max(np.abs(im.pixels(im.raw)[...,0]-self.l))),.00002)
        white=self.root/'white-zero.tif'
        tifffile.imwrite(white,65535-pixels,photometric='miniswhite')
        im=Image(white)
        self.assertLess(float(np.max(np.abs(im.pixels(im.raw)[...,0]-self.l))),.00002)
        planar=self.root/'planar.tif'
        tifffile.imwrite(planar,np.moveaxis(np.uint16(self.rgb*65535),-1,0),photometric='rgb',planarconfig='separate')
        im=Image(planar)
        self.assertLess(float(np.max(np.abs(im.pixels(im.raw)-self.rgb))),.00002)

    def test_mixed_scale_rotation_translation(self):
        # colour placed onto a luminance 2.29× finer and turned 8° (phase 6; register() is gone): scale estimated
        expected = np.vstack([cv2.getRotationMatrix2D((320,320), 8, 2.29), [0,0,1]])
        expected[:2,2] += [65,40]
        target = cv2.warpAffine(self.l, expected[:2], (1500,1450))
        tifffile.imwrite(self.lp, np.round(target*65535).astype(np.uint16))
        self.assertEqual(self.app.build(['L','RGB']), {'L':'built','RGB':'built'})
        report = json.loads(self.app.project.path(self.app.project.stream('colour')['report']).read_text())
        panel = next(iter(report['panels'].values()))
        self.assertEqual(panel['state'], 'placed')
        import colour_place
        pose = colour_place.Pose(panel['transform']['size'], panel['transform']['model'], panel['transform']['params'])
        corners = np.array([[80,80],[520,80],[80,520],[520,520]], float)
        error = np.linalg.norm(pose.to_canvas(corners) - (np.c_[corners, np.ones(4)] @ expected.T)[:,:2], axis=1)
        self.assertLess(float(error.max()), .7)
        self.assertAlmostEqual(report['k'], 2.29, places=2)
        self.assertEqual(self.app.project.match['matrix'], colour_place.K_matrix(report['k']).tolist())

    def test_smoothing_retains_luminance_detail(self):
        scene = self.built()
        m, size, _ = scene.geometry(DEFAULT_EDITS)
        a,_ = render(scene,m,size,DEFAULT_EDITS)
        edits = dict(DEFAULT_EDITS,smoothing=8)
        b,_ = render(scene,m,size,edits)
        la = cv2.cvtColor(a,cv2.COLOR_RGB2LAB)[...,0]
        lb = cv2.cvtColor(b,cv2.COLOR_RGB2LAB)[...,0]
        self.assertLess(float(np.mean(np.abs(la-lb))),.15)
        grey,_ = render(scene,m,size,dict(edits,strength=0))
        self.assertLess(float(np.max(np.ptp(grey,axis=2))),.002)

    def test_gaps_stay_grey_and_common_crop_avoids_holes(self):
        scene = self.built()
        scene.match[0,2] = 220
        rgb, info = render(scene,np.eye(3),(640,640),DEFAULT_EDITS)
        self.assertGreater(info['missing_colour'],30)
        self.assertLess(float(np.max(np.ptp(rgb[:,:200],axis=2))),.002)
        _,size,box = scene.geometry(dict(DEFAULT_EDITS,coverage='common'))
        self.assertGreaterEqual(box[0],220)
        self.assertLess(size[0],420)
        mask=np.ones((12,20),bool);mask[3:9,7:12]=False
        x0,y0,x1,y1=largest_rectangle(mask)
        self.assertTrue(mask[y0:y1,x0:x1].all())

    def test_tiff_png_quantization_and_shared_rotation(self):
        scene = self.built()
        edits = dict(DEFAULT_EDITS,rotation=17,smoothing=4,saturation=1.7)
        matrix,size,_=scene.geometry(edits)
        expected,_=render(scene,matrix,size,edits)
        for ext in ('tif','png'):
            path=self.root/f'export.{ext}'
            export(scene,path,edits)
            decoded=Image(path).pixels(Image(path).raw)
            self.assertEqual(decoded.shape,expected.shape)
            self.assertLess(float(np.mean(np.abs(decoded-expected))),.00025)
            self.assertLess(float(np.max(np.abs(decoded[250:260]-expected[250:260]))),.003)

    def test_cancel_does_not_publish_or_overwrite(self):
        scene=self.built();out=self.root/'cancel.tif'
        with self.assertRaises(InterruptedError):
            export(scene,out,DEFAULT_EDITS,cancelled=lambda:True)
        self.assertFalse(out.exists())
        self.assertFalse(list(self.root.glob('.cancel-*')))
        with self.assertRaises(ValueError):export(scene,self.lp,DEFAULT_EDITS)
        export(scene,out,DEFAULT_EDITS)
        before=out.read_bytes()
        with self.assertRaises(ValueError):export(scene,out,DEFAULT_EDITS)
        self.assertEqual(out.read_bytes(),before)

    def test_failed_set_keeps_other_mosaic(self):
        # colour is placed onto luminance (phase 6): with no luminance it cannot start, and says so
        self.lp.unlink()
        self.assertEqual(self.app.build(['L','RGB']),{'L':'failed','RGB':'failed'})
        self.assertIn('luminance mosaic first',self.app.project.stream('colour')['error'])
        self.assertIsNone(self.app.project.stream('luminance')['mosaic'])
        # and a colour placement that fails keeps the luminance mosaic
        tifffile.imwrite(self.lp,np.round(self.l*65535).astype(np.uint16))
        self.app.project.stream('luminance').update(state='ready',error=None)
        self.cp.unlink()
        self.assertEqual(self.app.build(['L','RGB']),{'L':'built','RGB':'failed'})
        self.assertTrue(self.app.project.path('derived/L.tif').is_file())
        self.assertIsNone(self.app.project.stream('colour')['mosaic'])

    def test_partial_import_keeps_valid_panels_and_reports_bad_files(self):
        bad=self.root/'unreadable.tif';bad.write_bytes(b'not an image')
        self.app.inputs('RGB',[str(self.cp),str(self.lp),str(bad)],allow_partial=True)
        s=self.app.project.set('RGB')
        self.assertEqual(s['inputs'],[str(self.cp.resolve())])
        self.assertEqual({v['name'] for v in s['skipped']},{'L.tif','unreadable.tif'})
        before=copy.deepcopy(s)
        with self.assertRaises(ValueError):self.app.inputs('RGB',[str(bad)],allow_partial=True)
        self.assertEqual(s,before)
        self.assertEqual(bad.read_bytes(),b'not an image')

    def test_recent_history_lists_without_mutating_projects_and_clears_only_history(self):
        self.built();p=self.app.project
        history=RecentProjects(self.root/'history'/'recent.json')
        history.remember(p.folder);history.remember(p.folder)
        p.stream('luminance')['state']='building';p.save()
        before=(p.folder/'project.json').read_bytes()
        self.assertEqual(len(history.read()),1)
        summary=history.summaries()[0]
        self.assertEqual(summary['panels'],{'L':1,'RGB':1})
        self.assertGreater(summary['bytes'],0)
        self.assertEqual((p.folder/'project.json').read_bytes(),before)
        history.write([])
        self.assertEqual(history.summaries(),[])
        self.assertTrue(p.path('derived/L.tif').exists())
        self.assertEqual((p.folder/'project.json').read_bytes(),before)

    def test_export_preview_coverage_override_matches_output_without_changing_project(self):
        scene=self.built();p=self.app.project
        before=copy.deepcopy(p.data)
        srv=start_server(0,app=self.app,token='preview-token')
        t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
        try:
            req=urllib.request.Request(srv.url()+f'api/export-preview?id={p.data["id"]}&coverage=common&factor=0.5&layer=luminance',headers={'X-LA-Token':'preview-token'})
            with urllib.request.urlopen(req) as r:info=json.load(r)
            self.assertTrue(info['preview'].startswith('data:image/png;base64,'))
            # the JPEG ceiling the dialog pre-flights with is the one export() enforces
            self.assertEqual((info['jpeg_max_side'],info['jpeg_max_pixels']),(lrgb.JPEG_MAX_SIDE,lrgb.JPEG_MAX_PIXELS))
            geometry,layer,factor,canvas,position,padding=self.app.export_options(p.data['geometry'],p.layers,dict(coverage='common',factor=.5,layer='luminance'))
            output=self.root/'preview-match.tif';scene.export(output,p.chain_doc(),geometry,p.layers,factor,layer,canvas=canvas,position=position,padding=padding)
            a=tifffile.imread(output)
            self.assertEqual(a.shape[:2],(info['height'],info['width']))
            self.assertEqual(info['raw_bytes'],a.nbytes)
            self.assertEqual(p.data,before)
            with self.assertRaises(ValueError):self.app.export_options(p.data['geometry'],p.layers,dict(factor=.1))
        finally:
            srv.shutdown();srv.server_close();t.join()

    def test_manual_fit_rejects_collinear_points(self):
        with self.assertRaises(ValueError):manual_match([[1,1],[2,2],[3,3]],[[1,1],[2,2],[3,3]])
        with self.assertRaises(ValueError):Project.validate_edits(dict(DEFAULT_EDITS,strength=float('nan')))

    def test_layer_exports_share_grid_and_jpeg_is_readable(self):
        scene=self.built()
        edits=dict(DEFAULT_EDITS,coverage='common',rotation=9)
        shapes=[]
        for layer in ('composite','luminance','colour'):
            out=self.root/f'{layer}.tif'
            export(scene,out,edits,factor=.5,layer=layer)
            image=tifffile.imread(out);shapes.append(image.shape)
            if layer=='luminance':self.assertLess(np.ptp(image.astype(int),axis=2).max(),120)
        self.assertEqual(shapes[0],shapes[1]);self.assertEqual(shapes[1],shapes[2])
        export(scene,self.root/'small.jpg',edits,factor=.5)
        self.assertEqual(Image(self.root/'small.jpg').raw.shape,shapes[0])

    def test_export_canvas_presets_pad_without_resampling_the_moon(self):
        scene=self.built();edits=dict(DEFAULT_EDITS,rotation=0)
        native=self.root/'native.tif';export(scene,native,edits)
        image=tifffile.imread(native)
        square=self.root/'square.tif';export(scene,square,edits,canvas='square')
        padded=tifffile.imread(square); side=max(image.shape[:2])
        self.assertEqual(padded.shape[:2],(side,side))
        top,left=(side-image.shape[0])//2,(side-image.shape[1])//2
        np.testing.assert_array_equal(padded[top:top+image.shape[0],left:left+image.shape[1]],image)
        wide=self.root/'wide.tif';export(scene,wide,edits,canvas='16:9')
        wide_image=tifffile.imread(wide)
        self.assertEqual(wide_image.shape[1]*9,wide_image.shape[0]*16)
        # The extension is a measured sky/background colour, not the renderer's old opaque black.
        self.assertGreater(float(wide_image[0].mean()),0)
        left=self.root/'left.tif';right=self.root/'right.tif'
        export(scene,left,edits,canvas='16:9',position='left');export(scene,right,edits,canvas='16:9',position='right')
        left_image,right_image=tifffile.imread(left),tifffile.imread(right)
        self.assertLess(np.argmax(left_image.mean(axis=(0,2))),np.argmax(right_image.mean(axis=(0,2))))
        custom=self.root/'custom.tif';export(scene,custom,edits,canvas='custom',padding=dict(left=17,right=31,top=11,bottom=23))
        self.assertEqual(tifffile.imread(custom).shape[:2],(image.shape[0]+34,image.shape[1]+48))
        with self.assertRaises(ValueError):self.app.export_options(self.app.project.data['geometry'],self.app.project.layers,dict(canvas='banana'))

    def test_levels_readouts_reach_the_editor(self):
        # BUGS.md bug 41: /api/analysis dropped Levels' histogram and its set-from-image levels
        self.built();p=self.app.project
        doc=p.chain_doc();doc['chains']['luminance'].append(chain.entry('levels'));p.set_chain(doc)
        block=p.data['chain']['chains']['luminance'][-1];self.assertEqual(block['type'],'levels')
        info=self.app.analysis()['blocks'][block['id']]
        self.assertEqual(len(info['histogram']),256)
        self.assertIn('highlight_level',info);self.assertIn('shadow_level',info)

    def test_export_canvas_centres_the_whole_disk(self):
        # BUGS.md bug 42: a crescent mosaic covers only the lit side; "Moon centred" centres the fitted disk
        moon=lunar_disk(640,mono=True,crescent=True)
        x0=int(np.flatnonzero(moon['lit'].any(0))[0])-8
        tifffile.imwrite(self.lp,np.ascontiguousarray(moon['image'][:,x0:]))
        self.app.project.set('RGB')['inputs']=[];self.app.project.save()
        self.assertEqual(self.app.build(['L']),{'L':'built'})
        scene=self.app.get_scene()
        self.assertIsNotNone(scene.disk())
        cx,cy,r=scene.disk();self.assertAlmostEqual(r,moon['radius_px'],delta=4)
        geometry=geo(dict(DEFAULT_EDITS,rotation=0))
        for position,f in (('centre',.5),('left',1/3),('right',2/3)):
            m,(ow,oh),_,(left,top,w,h)=scene.canvas_geometry(geometry,canvas='16:9',position=position)
            px,py=(m@[cx,cy,1])[:2]
            self.assertEqual(ow*9,oh*16);self.assertLessEqual(left+w,ow);self.assertGreaterEqual(left,0)
            self.assertLess(abs(px-(ow*f-.5)),1.01,position);self.assertLess(abs(py-(oh/2-.5)),1.01,position)
            self.assertGreaterEqual(min(px,ow-px,py,oh-py),r-1,position)     # the whole disk is in the frame
        # without a limb the mosaic's own box is centred, as before
        scene._disk=None
        m,(ow,oh),_,(left,top,w,h)=scene.canvas_geometry(geometry,canvas='16:9')
        self.assertLessEqual(abs(2*left-(ow-w)),1)

    def test_export_replaces_a_file_only_when_asked(self):
        scene=self.built();edits=dict(DEFAULT_EDITS,rotation=0);out=self.root/'again.tif'
        export(scene,out,edits);first=out.stat().st_ino
        with self.assertRaises(lrgb.ExportExists):export(scene,out,edits,factor=.5)
        export(scene,out,edits,factor=.5,overwrite=True)
        self.assertEqual(tifffile.imread(out).shape[0],320);self.assertNotEqual(out.stat().st_ino,first)
        with self.assertRaisesRegex(ValueError,'folder'):export(scene,self.root,edits,overwrite=True)

    def test_upload_validates_mono_and_survives_project_move(self):
        srv=start_server(0,app=self.app,token='upload-token')
        t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
        # Reset the references, so the upload is the complete L input set.
        self.app.project.set('L')['inputs']=[]
        self.app.project.save()
        pid=self.app.project.data['id']
        raw=self.lp.read_bytes()
        def upload(key,name,body):
            req=urllib.request.Request(srv.url()+f'api/upload?id={pid}&set={key}&name={name}',
                data=body,headers={'X-LA-Token':'upload-token'})
            return urllib.request.urlopen(req)
        try:
            with self.assertRaises(urllib.error.HTTPError):upload('RGB','mono.tif',raw)
            self.assertFalse(list((self.app.project.folder/'sources'/'RGB').glob('*')))
            with upload('L','panel.tif',raw) as r: self.assertEqual(r.status,200)
            relative=self.app.project.set('L')['inputs'][0]
            self.assertTrue(relative.startswith('sources/'))
            old=self.app.project.folder;self.app.close()
            new=self.root/'moved.lunarmosaic';old.rename(new)
            self.app.select(new)
            self.assertTrue(Path(self.app.project.input_paths('L')[0]).is_file())
            self.assertEqual(self.app.build(['L']),{'L':'built'})
        finally:
            srv.shutdown();srv.server_close();t.join()

    def test_upload_does_not_hold_the_application_lock_while_reading(self):
        """A slow browser body must not block status, cancellation, or another request."""
        self.app.project.set('L')['inputs']=[]
        self.app.project.save()
        raw=self.lp.read_bytes(); started=threading.Event(); release=threading.Event()
        class SlowReader:
            def __init__(self, data): self.data=data; self.first=True
            def read(self, n):
                if self.first:
                    self.first=False; started.set(); release.wait(3)
                result,self.data=self.data[:n],self.data[n:]
                return result
        class Handler:
            def __init__(self, outer):
                self.path=f'/api/upload?id={outer.app.project.data["id"]}&set=L&name=panel.tif'
                self.rfile=SlowReader(raw); self.reply=None
            def length(self, limit):
                if len(raw)>limit: raise ValueError('too large')
                return len(raw)
            def json(self, value, status=200): self.reply=(value,status)
        h=Handler(self)
        worker=threading.Thread(target=lambda: self.app.post(h,'/api/upload'))
        worker.start();self.assertTrue(started.wait(2))
        acquired=self.app.lock.acquire(timeout=.3)
        if acquired:self.app.lock.release()
        self.assertTrue(acquired, 'the upload body should be read without the application lock')
        release.set();worker.join(5)
        self.assertFalse(worker.is_alive());self.assertEqual(h.reply[1],200)
        self.assertEqual(len(self.app.project.set('L')['inputs']),1)

    def test_failed_colour_publication_keeps_the_previous_canvas_report_pair(self):
        self.built(); p=self.app.project
        old=copy.deepcopy(p.stream('colour'))
        old_canvas=p.path(old['mosaic']).read_bytes();old_report=p.path(old['report']).read_bytes()
        partial=p.path('derived/.colour_canvas.partial.tif');partial_report=p.path('derived/.colour_place.partial.json')
        def fake_place(command, progress):
            shutil.copyfile(p.path(old['mosaic']),partial)
            shutil.copyfile(p.path(old['report']),partial_report)
            return 0,''
        real_replace=app_module.os.replace
        def fail_report(source,destination):
            if Path(source)==partial_report: raise OSError('simulated report publication failure')
            return real_replace(source,destination)
        with patch.object(self.app,'run_process',fake_place),patch.object(app_module.os,'replace',fail_report):
            with self.assertRaises(OSError):self.app.place_colour()
        current=p.stream('colour')
        self.assertEqual((current['mosaic'],current['report']),(old['mosaic'],old['report']))
        self.assertEqual(p.path(current['mosaic']).read_bytes(),old_canvas)
        self.assertEqual(p.path(current['report']).read_bytes(),old_report)
        self.assertEqual(current['state'],'failed')
        self.assertFalse(partial.exists());self.assertFalse(partial_report.exists())
        self.assertEqual(list((p.folder/'derived').glob('colour_canvas.*.tif')),[p.path(old['mosaic'])])

    def test_second_process_lease_and_interrupted_recovery(self):
        other=Application()
        with self.assertRaises(ValueError):other.select(self.app.project.folder)
        other.close()
        self.app.project.stream('luminance')['state']='building';self.app.project.save();self.app.close()
        p=Project.open(self.root/'moon.lunarmosaic')
        self.assertEqual(p.stream('luminance')['state'],'interrupted')

    def test_missing_colour_reopens_luminance_without_stale_common_crop(self):
        self.built();p=self.app.project
        p.set_geometry(dict(coverage='common'));p.set_view(dict(l_visible=False));p.save();self.app.close()
        p.path(p.stream('colour')['mosaic']).rename(p.path('derived/colour_canvas.moved.tif'))
        self.app.select(p.folder)
        restored=self.app.project
        self.assertEqual(restored.stream('colour')['state'],'missing')
        self.assertIsNone(restored.match)
        self.assertEqual(restored.data['geometry']['coverage'],'full')
        self.assertTrue(restored.layers['l_visible'])
        pixels,info=self.app.get_scene().preview(restored.chain_doc(),restored.data['geometry'],restored.layers)
        self.assertEqual((info['width'],info['height']),(640,640))
        self.assertLess(np.ptp(pixels,axis=2).max(),.001)

    def test_http_auth_stale_edits_and_native_viewport(self):
        self.built()
        srv=start_server(0,app=self.app,token='test-token')
        t=threading.Thread(target=srv.serve_forever,daemon=True);t.start()
        base=srv.url()
        def request(path,data=None,token=True,origin=None):
            headers={'X-LA-Token':'test-token'} if token else {}
            if origin:headers['Origin']=origin
            if data is not None:headers['Content-Type']='application/json'
            req=urllib.request.Request(base+path,data=None if data is None else json.dumps(data).encode(),headers=headers)
            return urllib.request.urlopen(req)
        try:
            with self.assertRaises(urllib.error.HTTPError):request('api/state',token=False)
            with self.assertRaises(urllib.error.HTTPError):request('api/state',origin='https://evil.example')
            p=self.app.project.data
            with self.assertRaises(urllib.error.HTTPError) as caught:
                request('api/chain',dict(id=p['id'],revision=-1,chain=chain.empty()))
            self.assertEqual(caught.exception.code,409)
            with request(f'api/viewport?id={p["id"]}&x=20&y=30&w=400&h=300&scale=1') as r:
                image=cv2.imdecode(np.frombuffer(r.read(),np.uint8),cv2.IMREAD_UNCHANGED)
                self.assertEqual(image.shape,(300,400,4))
            # BUGS.md 38: outside the canvas the workspace is transparent, so the page's own background shows
            with request(f'api/viewport?id={p["id"]}&x=-50&y=0&w=200&h=100&scale=1') as r:
                image=cv2.imdecode(np.frombuffer(r.read(),np.uint8),cv2.IMREAD_UNCHANGED)
                self.assertEqual(int(image[:,:40,3].max()),0)
                self.assertGreater(int(image[:,60:,3].min()),0)
            with request(f'api/layer-thumbnail?id={p["id"]}&set=L') as r:
                image=cv2.imdecode(np.frombuffer(r.read(),np.uint8),cv2.IMREAD_UNCHANGED)
                self.assertLessEqual(max(image.shape[:2]),96)
                self.assertEqual(int(np.ptp(image,axis=2).max()),0)
        finally:
            srv.shutdown();srv.server_close();t.join()


if __name__=='__main__':
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(Workflow)
    class Percent(unittest.TextTestResult):
        def startTest(self,test):
            print(f'[{self.testsRun/ suite.countTestCases():.0%}] {test._testMethodName}',flush=True)
            super().startTest(test)
    result=unittest.TextTestRunner(verbosity=1,resultclass=Percent).run(suite)
    print('[100%] LRGB workflow checks complete')
    raise SystemExit(not result.wasSuccessful())
