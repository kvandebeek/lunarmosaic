"""Windowed entry point for the packaged LunarMosaic application."""
import sys


def main():
    from lunarmosaic import main as run
    args = sys.argv[1:]
    run(args if args else ['app'])


if __name__ == '__main__':
    main()
