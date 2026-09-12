"""Frozen executable entry point and private worker entry point."""
import sys

if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--pdf2ai-worker":
        from pdf2ai.workers.worker_cli import run_job

        raise SystemExit(run_job(sys.argv[2]))
    if len(sys.argv) == 3 and sys.argv[1] == "--self-test":
        from pdf2ai.self_test import run

        raise SystemExit(run(sys.argv[2]))
    from pdf2ai.main import main

    raise SystemExit(main())
