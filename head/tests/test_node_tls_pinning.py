"""TLS до ноды: доверяем сертификату, а не имени в нём.

marzban-node выпускает себе самоподписанный сертификат при первом запуске
и адреса ноды не знает — в сертификате его нет. Голова этот сертификат
пиннит, но `verify=<файл>` в httpx означает «считать его удостоверяющим
центром», а это включает сверку имени. Она падала с «IP address mismatch»,
и канал управления не поднимался ни разу за всю жизнь ноды: контейнер
работает, порт слушается, а голова недостижима.

Здесь проверяется, что снятие сверки имени не сняло саму проверку. Это и
есть та граница, за которой «пиннинг» превращается в «доверяем чему
угодно»: соединение к сертификату без нужного имени должно проходить, а к
чужому — нет.
"""

from __future__ import annotations

import socket
import ssl

import pytest

from app.node_manager.channel import NodeCertBundle, _ssl_context
from tests.tls_server import make_cert, tls_server


def handshake(context: ssl.SSLContext, port: int) -> None:
    """Довести TLS до конца — или бросить причину, по которой не вышло.

    Проверяется именно рукопожатие: тестовый слушатель по-HTTP не говорит,
    и запрос через httpx спотыкался бы уже после успешной проверки
    сертификата, размывая то, что здесь важно.
    """
    with (
        socket.create_connection(("127.0.0.1", port), timeout=5) as raw,
        context.wrap_socket(raw, server_hostname="127.0.0.1"),
    ):
        pass


@pytest.fixture
def head_identity(tmp_path):
    """Клиентский сертификат головы — им она представляется ноде."""
    return make_cert(tmp_path, "head")


def bundle(node_cert, head: object) -> NodeCertBundle:
    return NodeCertBundle(
        ca_cert=str(node_cert.cert),
        client_cert=str(head.cert),
        client_key=str(head.key),
    )


def test_a_pinned_certificate_without_the_right_name_is_accepted(tmp_path, head_identity):
    """Собственно поломка: сертификат ноды не назван её адресом.

    До исправления соединение падало с «IP address mismatch», и нода
    оставалась изолированной навсегда.
    """
    node = make_cert(tmp_path, "some-other-name", san="DNS:not-the-node")
    context = _ssl_context(bundle(node, head_identity))

    # Адрес намеренно не совпадает ни с CN, ни с SAN сертификата — именно
    # так выглядит настоящая нода.
    with tls_server(node) as port:
        handshake(context, port)


def test_a_different_certificate_is_still_refused(tmp_path, head_identity):
    """Граница. Без неё «пиннинг» означал бы отсутствие проверки.

    Сервер предъявляет валидный, но не тот сертификат — соединение должно
    оборваться, иначе подменить ноду сможет кто угодно.
    """
    pinned = make_cert(tmp_path, "pinned-node")
    impostor = make_cert(tmp_path, "impostor")
    context = _ssl_context(bundle(pinned, head_identity))

    with tls_server(impostor) as port, pytest.raises(ssl.SSLCertVerificationError):
        handshake(context, port)


def test_verification_itself_stays_on(tmp_path, head_identity):
    """Прямая проверка настроек контекста.

    check_hostname=False допустимо только вместе с CERT_REQUIRED: снять
    заодно и это — ровно та ошибка, от которой отличается пиннинг.
    """
    node = make_cert(tmp_path, "node")
    context = _ssl_context(bundle(node, head_identity))

    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is False


def test_the_head_presents_its_own_certificate(tmp_path, head_identity):
    """mTLS в обратную сторону: нода проверяет, что пришли мы.

    Пиннинг защищает голову от подменённой ноды; клиентский сертификат —
    ноду от кого угодно ещё. Потерять вторую половину, чиня первую, было
    бы легко.
    """
    node = make_cert(tmp_path, "node")
    context = _ssl_context(bundle(node, head_identity))

    assert context.get_ca_certs(), "пиннинг: сертификат ноды должен быть загружен"

    # load_cert_chain не отдаёт цепочку обратно, поэтому убеждаемся иначе:
    # без клиентского сертификата контекст вообще не строится, а значит
    # построенный его несёт.
    broken = NodeCertBundle(
        ca_cert=str(node.cert),
        client_cert=str(tmp_path / "нет-такого.pem"),
        client_key=str(tmp_path / "нет-такого.key"),
    )
    with pytest.raises(OSError):
        _ssl_context(broken)
