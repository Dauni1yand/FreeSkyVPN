# Нативные библиотеки

Две библиотеки не тянутся из Maven и кладутся сюда вручную. Пока их нет,
приложение собирается и запускается, но подключиться не сможет — и скажет
об этом, а не упадёт с ошибкой линковки.

## 1. libXray (ядро Xray)

Официальная обёртка XTLS вокруг Xray-core, лицензия **MPL-2.0**.

    https://github.com/XTLS/libXray  →  releases  →  libXray.aar

Положить в эту папку как `libXray.aar`.

Собрать самому (нужен Go 1.21+ и Android NDK):

```bash
git clone https://github.com/XTLS/libXray && cd libXray
go install golang.org/x/mobile/cmd/gomobile@latest
gomobile init
gomobile bind -target=android -androidapi 26 -o libXray.aar ./
```

**Почему не AndroidLibXrayLite от v2rayNG.** Она удобнее и популярнее, но
лицензирована под GPL-3.0. Связывание с ней обязало бы открыть исходники
этого приложения на тех же условиях. libXray под MPL-2.0 такого требования
не создаёт.

## 2. hev-socks5-tunnel (tun2socks)

У Xray-core нет tun-инбаунда, поэтому пакеты с tun-устройства кто-то должен
превращать в потоки и отдавать в SOCKS. Лицензия **MIT**.

    https://github.com/heiher/hev-socks5-tunnel

Собранные `.so` для четырёх ABI кладутся в
`app/src/main/jniLibs/{arm64-v8a,armeabi-v7a,x86,x86_64}/libhev-socks5-tunnel.so`.

## 3. geoip.dat и geosite.dat

Нужны Xray, чтобы правило `geoip:ru` вообще что-то находило. Без них
доменные правила работают, а правила по IP молча не совпадают ни с чем —
то есть часть российского трафика уйдёт в туннель.

    https://github.com/Loyalsoldier/v2ray-rules-dat  →  releases

Положить в `app/src/main/assets/geoip.dat` и `app/src/main/assets/geosite.dat`.
