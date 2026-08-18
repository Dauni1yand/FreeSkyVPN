# Провижининг ноды

Единственное место, где вообще используется SSH — дальше управление идёт
только через `app/node_manager` (REST, напрямую или через Reality-туннель).

```bash
# один раз для всей головы
./generate_head_client_cert.sh /etc/freeskyvpn/

# на каждую новую ноду
python3 provision_node.py \
    --host 203.0.113.10 \
    --country nl \
    --client-cert /etc/freeskyvpn/head_client_cert.pem \
    --api-url http://localhost:8000
```

`provision_node.py`:
1. копирует клиентский сертификат головы на ноду (`scp`);
2. запускает `bootstrap_node.sh` на ноде по SSH — ставит Docker, xray-core
   (только ради `xray x25519`/`xray uuid` для генерации ключей), генерирует
   Reality-keypair для **отдельного, невидимого пользователям** inbound,
   через который позже пойдёт туннель управления при блокировке прямого
   канала, поднимает marzban-node в режиме REST;
3. читает JSON, который скрипт печатает в конце, и регистрирует ноду через
   `POST /api/v1/nodes/register` на голове.

Обычные (клиентские) inbound'ы этим скриптом не создаются — их порт/SNI/
transport подбирает Config Selector на голове (следующая фаза).
