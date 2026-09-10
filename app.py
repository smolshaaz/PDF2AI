"""Frozen executable entry point (also supports a local packaging self-test)."""
import multiprocessing

if __name__ == "__main__":
    multiprocessing.freeze_support()
    import sys
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from pdf2ai.self_test import run
        raise SystemExit(run(sys.argv[2]))
    from pdf2ai.main import main
    raise SystemExit(main())
