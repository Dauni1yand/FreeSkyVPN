"""Operator commands that must work before anyone can log in.

    python -m app.cli create-admin <username> [password]
    python -m app.cli generate-key
    python -m app.cli add-node <host> <country> <ssh-password> [опции]
    python -m app.cli list-nodes
    python -m app.cli egress-url

`create-admin` is the bootstrap: the panel has no sign-up, so the first
operator has to be made from a shell on the head. It also resets an
existing operator's password, which is the recovery path when someone is
locked out.

`add-node` is the same for the fleet. The web panel can do it too and
needs no Telegram to — but reaching a panel bound to loopback means an SSH
tunnel and a browser, and on a server being set up over SSH that is a
detour. Everything the form does, this does.
"""

from __future__ import annotations

import argparse
import secrets
import sys

from sqlalchemy import select

from app.db.models.node import Node
from app.db.session import SessionLocal
from app.services import egress, provisioning
from app.services.admin_auth import ensure_admin
from app.services.config_selector import NoCapacityError, assign_config
from app.services.ssh_manager import SshError


def create_admin(argv: list[str]) -> int:
    if not argv:
        print("usage: python -m app.cli create-admin <username> [password]", file=sys.stderr)
        return 2

    username = argv[0]
    password = argv[1] if len(argv) > 1 else secrets.token_urlsafe(18)
    generated = len(argv) < 2

    with SessionLocal() as db:
        ensure_admin(db, username, password)
        db.commit()

    print(f"admin '{username}' ready")
    if generated:
        print(f"password: {password}")
        print("Store it now — it is not recoverable, only resettable by re-running this command.")
    return 0


def generate_key(_argv: list[str]) -> int:
    """A key for SECRETS_KEY / HEAD_SECRET_KEY."""
    print(secrets.token_urlsafe(48))
    return 0


def add_node(argv: list[str]) -> int:
    """Provision a node without a browser.

    Takes the same fields as the panel's form. The SSH password is used
    once, to install the head's key and run the bootstrap; the node's
    password is replaced with a random one before this returns, so the one
    typed here stops working — which is the point.
    """
    parser = argparse.ArgumentParser(prog="add-node", description=add_node.__doc__)
    parser.add_argument("host", help="IP или имя ноды")
    parser.add_argument("country", help="метка страны, пользователям не видна")
    parser.add_argument("ssh_password", help="пароль root от хостера, используется один раз")
    parser.add_argument("--ssh-user", default="root")
    parser.add_argument("--ssh-port", type=int, default=22)
    parser.add_argument("--uplink-mbit", type=int, default=100)
    parser.add_argument("--capacity", type=int, default=200)
    parser.add_argument("--control-sni", default="www.microsoft.com")
    args = parser.parse_args(argv)

    print(f"подключаю {args.host} — это 1–3 минуты", flush=True)
    with SessionLocal() as db:
        try:
            result = provisioning.provision_node(
                db,
                host=args.host.strip(),
                country=args.country.strip(),
                ssh_user=args.ssh_user.strip(),
                ssh_password=args.ssh_password,
                ssh_port=args.ssh_port,
                uplink_mbit=args.uplink_mbit,
                capacity=args.capacity,
                control_sni=args.control_sni.strip(),
            )
        except (provisioning.ProvisioningError, SshError) as exc:
            # Committed rather than rolled back: the half-provisioned row and
            # its failure log are how the next attempt knows where it stopped.
            db.commit()
            print(f"\nне удалось подключить {args.host}: {exc}", file=sys.stderr)
            return 1
        db.commit()

    for line in result.log:
        print(f"  · {line}")
    print(f"\nнода {args.host} подключена")
    print("Пароль на ноде сменён на случайный — введённый больше не действует.")
    return 0


def list_nodes(_argv: list[str]) -> int:
    with SessionLocal() as db:
        nodes = db.scalars(select(Node).order_by(Node.country, Node.host)).all()
        if not nodes:
            print("нод нет")
            return 0
        width = max(len(n.host) for n in nodes)
        for node in nodes:
            print(
                f"{node.host:<{width}}  {node.country:<14} "
                f"{node.status.value:<10} канал:{node.channel_state.value:<9} "
                f"ёмкость:{node.capacity:<5} {node.uplink_mbit or '?'} Мбит"
            )
    return 0


def egress_url(_argv: list[str]) -> int:
    """Print a vless:// link for the egress proxy.

    Only needed to pin the proxy to a fixed server. Left to itself the
    proxy asks the head over the API and re-asks when its node stops
    working, which is what survives a node being blocked.
    """
    with SessionLocal() as db:
        user = egress.get_or_create(db)
        try:
            config = assign_config(db, user)
        except NoCapacityError as exc:
            print(f"нет свободной ноды: {exc}", file=sys.stderr)
            print("Добавьте ноду и повторите: python -m app.cli add-node …", file=sys.stderr)
            return 1
        db.commit()

    print(config.vless_url)
    print(f"\nНода: {config.node_country}", file=sys.stderr)
    return 0


COMMANDS = {
    "create-admin": create_admin,
    "generate-key": generate_key,
    "add-node": add_node,
    "list-nodes": list_nodes,
    "egress-url": egress_url,
}


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"usage: python -m app.cli {{{'|'.join(COMMANDS)}}} [args]", file=sys.stderr)
        return 2
    return COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    raise SystemExit(main())
