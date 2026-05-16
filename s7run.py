#!/usr/bin/env python3
import signal
import sys
import time
from s7_server import S7Server


def main():
    port = 1105
    rack = 0
    slot = 1
    sim_db = 1
    sim_interval = 1.0

    s7 = S7Server(tcp_port=port, rack=rack, slot=slot)

    def shutdown(signum, frame):
        print(f"\nSignal {signum} received, shutting down...")
        s7.stop_simulation()
        s7.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    s7.start_simulation(db_number=sim_db, interval=sim_interval)

    try:
        s7.start()
    except Exception as e:
        print(f"Failed to start S7 server: {e}")
        sys.exit(1)

    print(f"Simulation running on DB{sim_db}")
    print("Press Ctrl+C to stop")

    try:
        while True:
            stats = s7.get_sim_statistics()
            print(f"  counter={stats['counter']:>5}  tags={stats['tags']}  running={stats['running']}")
            time.sleep(5)
    except KeyboardInterrupt:
        pass
    finally:
        s7.stop_simulation()
        s7.stop()


if __name__ == "__main__":
    main()
