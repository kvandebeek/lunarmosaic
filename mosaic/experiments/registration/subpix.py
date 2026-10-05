import os, sys,glob,numpy as np,cv2
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), '..', '..')); import mosaic_common as mc
rng=np.random.default_rng(0); T=256; win=cv2.createHanningWindow((T,T),cv2.CV_32F)
def lum(p):
    x,_,_=mc.read_image(p); return mc.luminance(x).astype(np.float32)
def shift(img,dx,dy):
    M=np.float32([[1,0,dx],[0,1,dy]]); return cv2.warpAffine(img,M,(img.shape[1],img.shape[0]),flags=cv2.INTER_LANCZOS4)
def pc(a,b): (sx,sy),r=mc.phase_shift(a-a.mean(),b-b.mean(),win); return np.array([sx,sy]),r
def pc_iter(a,b,n=3):
    s=np.zeros(2)
    for _ in range(n):
        bw=shift(b,-s[0],-s[1]); d,r=pc(a,bw); s=s+d
    return s,r
def upsampled(a,b,up=20):
    # Guizar-Sicairos style: whitened cross-power, integer peak, then local DFT upsampling around it
    A=np.fft.fft2((a-a.mean())*win); B=np.fft.fft2((b-b.mean())*win); R=A.conj()*B; R/=np.abs(R)+1e-9
    c=np.fft.ifft2(R).real; py,px=np.unravel_index(np.argmax(c),c.shape)
    py=py-T if py>T//2 else py; px=px-T if px>T//2 else px
    n=np.fft.fftfreq(T); rng_=np.arange(-1.5,1.5+1e-9,1/up)
    ky=np.exp(2j*np.pi*np.outer(py+rng_,n)); kx=np.exp(2j*np.pi*np.outer(n,px+rng_))
    cc=(ky@R@kx).real; iy,ix=np.unravel_index(np.argmax(cc),cc.shape)
    return np.array([px+rng_[ix],py+rng_[iy]]),cc.max()/R.size


def main():
    paths=sorted(glob.glob(os.environ.get('MOSAIC_PANELS', 'panels') + '/*.tif'))
    methods={'phaseCorrelate':pc,'phaseCorrelate x3 iter':pc_iter,'upsampled DFT x20':upsampled}
    err={k:[] for k in methods}
    for p in rng.choice(paths,6,replace=False):
        L=lum(p); h,w=L.shape
        for _ in range(25):
            y0,x0=rng.integers(300,h-300-T),rng.integers(300,w-300-T)
            a=L[y0:y0+T,x0:x0+T].copy()
            if a.std()<0.01: continue
            dx,dy=rng.uniform(-3,3,2)
            big=L[y0-20:y0+T+20,x0-20:x0+T+20]
            b=shift(big,dx,dy)[20:20+T,20:20+T]           # content moves by +d
            s1,s2=rng.uniform(0.6,1.4,2)                     # different seeing blur per panel
            a2=cv2.GaussianBlur(a,(0,0),s1)+rng.normal(0,0.002,a.shape).astype(np.float32)
            b2=cv2.GaussianBlur(b,(0,0),s2)+rng.normal(0,0.002,a.shape).astype(np.float32)
            for k,fn in methods.items():
                s,_=fn(a2,b2); err[k].append(np.hypot(s[0]-dx,s[1]-dy))
    for k,v in err.items():
        v=np.array(v); print(f'{k:24s} n={len(v)} rms {np.sqrt((v**2).mean()):.3f} px  median {np.median(v):.3f}  p90 {np.percentile(v,90):.3f}')


if __name__ == '__main__':
    main()
