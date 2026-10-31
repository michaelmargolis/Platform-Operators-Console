    args = parser.parse_args()
    try:
        import obsws_python as obs
    except ImportError as exc:
        parser.error("Install OBS client with: pip install obsws-python")

    rides = tuple(config.RIDES)
    match = [i for i, (name, _) in enumerate(rides) if name.casefold() == "blackbird"]
    if len(match) != 1:
        parser.error("Blackbird was not found uniquely in NL2 config.RIDES")
    index = match[0]

    # These are the arguments expected by the existing capture_one function.
    capture_args = argparse.Namespace(
        host=args.nl2_host,
        hz=20.0,
        prepare_timeout=90.0,
        max_duration=args.max_duration,
        output_dir=ROOT / "captures" / "nl2_ride_study",
        state_monitor=False,
    )
    sim = Sim(address=args.nl2_host, startup_ride_index=index)
    obs_client = None
    recording_started = False
    try:
        connect_recorder(sim, capture_args.prepare_timeout, 1.0 / capture_args.hz)
        obs_client = obs.ReqClient(host="127.0.0.1", port=args.obs_port,
                                   password=args.obs_password, timeout=5)
        if obs_client.get_record_status().output_active:
            raise RuntimeError("OBS is already recording; stop it before starting this test")
        print("Starting OBS recording; Blackbird will be prepared and dispatched...")
        obs_client.start_record()
        recording_started = True
        print("OBS start requested:", datetime.now(timezone.utc).isoformat(),
              "monotonic:", time.monotonic())
        # This existing production adapter path loads/prepares/dispatches the ride,
        # writes the raw telemetry + state-events CSVs, and returns on READY.
        csv_path = capture_one(sim, index, 1, capture_args, rides)
        print("Ride completed:", csv_path)
        return 0
    finally:
        if recording_started:
            try:
                stopped = obs_client.stop_record()
                print("OBS recording saved:", stopped.output_path)
                print("OBS stop requested:", datetime.now(timezone.utc).isoformat(),
                      "monotonic:", time.monotonic())
            except Exception as exc:
                print("WARNING: OBS recording could not be stopped:", exc,
                      file=sys.stderr)
        sim.disconnect()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except KeyboardInterrupt:
        print("Interrupted by user; closing connections.", file=sys.stderr)
        raise SystemExit(130)
