"""python -m client [--data-dir PATH] [--headless] [--once]."""
import argparse
import signal
from client.agent import Agent
from client.instance import SingleInstance
from client.state import default_data_dir


def main(argv=None):
    parser = argparse.ArgumentParser(description='TracerTransfer Windows client')
    parser.add_argument('--data-dir', default=str(default_data_dir()))
    parser.add_argument('--headless', action='store_true')
    parser.add_argument('--background', action='store_true', help='Abre na bandeja, sem manter janela visível.')
    parser.add_argument('--once', action='store_true', help='Uma observação/envio; estabilidade ainda exige duas rodadas.')
    parser.add_argument('--apply-update', metavar='REQUEST_DIR', help='Modo interno: aplica solicitação assinada e encerra.')
    args = parser.parse_args(argv)
    if args.apply_update:
        from updates.windows import client_update_main
        return client_update_main(args.apply_update)
    with SingleInstance(args.data_dir):
        agent = Agent(args.data_dir)
        from updates.runtime import installed_client
        agent.auto_update = installed_client(args.data_dir, once=args.once)
        def stop(*_):
            agent.stop_event.set()
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)
        try:
            if args.once:
                print(agent.run_once().to_json())
            elif args.headless:
                agent.run()
            else:
                from client.config_ui import ClientUI
                ui = ClientUI(agent)
                if args.background:
                    ui.root.after(500, ui.hide)
                ui.run()
        finally:
            agent.close()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
