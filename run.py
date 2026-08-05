import sys

if __name__ == "__main__":
    if "--entity-index-worker" in sys.argv:
        from diffasaurus.core.entity.index_worker import main as worker_main

        worker_argv = [arg for arg in sys.argv[1:] if arg != "--entity-index-worker"]
        raise SystemExit(worker_main(worker_argv))
    from diffasaurus.ui.main_window import main

    main()
