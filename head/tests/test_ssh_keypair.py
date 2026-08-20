"""Ключ, которым голова заходит на ноду после первого подключения.

Функция чистая — ни сети, ни диска, — и именно поэтому её отсутствие в
тестах ничем себя не выдавало. `paramiko.Ed25519Key.generate()` выглядит
ровно как `RSAKey.generate()` и `ECDSAKey.generate()`, но такого метода у
Ed25519Key нет. AttributeError вылезал на сервере, в момент, когда
провижининг уже решил, что нода доступна, и откатываться поздно.

Проверяется круг целиком: сгенерировать, прочитать тем же способом, каким
это делает `connect`, и сверить публичную строку с приватным ключом. По
отдельности каждая половина может выглядеть правдоподобно и не подходить
к другой.
"""

from __future__ import annotations

import io

import paramiko

from app.services.ssh_manager import generate_keypair


def test_a_keypair_is_produced_at_all():
    """Регрессия: здесь падало с AttributeError."""
    private_pem, public_line = generate_keypair()
    assert private_pem
    assert public_line


def test_the_private_half_is_in_the_format_connect_reads_back():
    """`connect` разбирает его через Ed25519Key.from_private_key.

    Ключ в неподходящем формате означал бы ноду, на которую голова больше
    не может зайти, — и заметно это стало бы только при следующей операции.
    """
    private_pem, _ = generate_keypair()
    assert private_pem.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")

    key = paramiko.Ed25519Key.from_private_key(io.StringIO(private_pem))
    assert key.get_name() == "ssh-ed25519"


def test_the_public_line_matches_the_private_key():
    """Обе половины могут быть правдоподобны по отдельности и не подходить
    друг к другу; тогда ключ ляжет на ноду, а зайти по нему не выйдет."""
    private_pem, public_line = generate_keypair()
    key = paramiko.Ed25519Key.from_private_key(io.StringIO(private_pem))

    algorithm, encoded, comment = public_line.split()
    assert algorithm == key.get_name()
    assert encoded == key.get_base64()
    assert comment == "freeskyvpn-head"


def test_the_public_line_is_a_single_authorized_keys_entry():
    """Уходит в файл построчно: перевод строки внутри сломал бы разбор."""
    _, public_line = generate_keypair()
    assert "\n" not in public_line
    assert len(public_line.split()) == 3


def test_every_node_gets_its_own_key():
    """Один ключ на весь флот означал бы, что компрометация одной ноды —
    это доступ ко всем."""
    first, _ = generate_keypair()
    second, _ = generate_keypair()
    assert first != second
