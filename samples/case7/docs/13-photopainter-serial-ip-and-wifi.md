# PhotoPainter 串口读取 IP 与 Wi-Fi 配网手册

_适用设备：Waveshare ESP32-S3-PhotoPainter 7.3 英寸；本次实测固件 v2.18.0。_

## 目标与地址含义

PhotoPainter 的网页地址由路由器通过 DHCP 分配，刷写固件后不能凭设备名称或旧记录猜测。串口启动日志是确认地址的首选证据。需要区分以下三类地址：

| 名称 | 示例 | 含义 |
| --- | --- | --- |
| 配网 AP | `PhotoFrame - AA1DC` | 设备尚未保存 Wi-Fi 凭据时临时创建的 2.4 GHz 网络，不是家庭局域网地址 |
| STA 局域网 IP | `192.168.1.137` | 设备连接路由器后由 DHCP 分配的实际网页地址 |
| mDNS 名称 | `photoframe.local` | 可选的局域网名称解析；Windows、路由器或 VPN 不一定支持 |

`192.168.1.117` 是旧的 Seeed reTerminal E1002 地址，不能用于当前微雪设备。`192.168.1.135` 是 Ascend 310B 服务地址，也不是 PhotoPainter 地址。

注意拼写：固件广播的是 `photoframe.local`（`photo` 后有字母 **o**）；`phtoframe.local`
不是设备公布的名称。即使拼写正确，mDNS 仍可能因 Windows 网络配置、VPN 或路由器策略而不可用，
所以教材和排障流程始终以串口输出的 IPv4 地址为准。

## Windows 电脑准备

1. 只连接当前微雪 PhotoPainter 的 USB 线，暂时不要把 E1002 接到同一台电脑。
2. 打开 PowerShell，枚举 USB 串口：

```powershell
Get-CimInstance Win32_SerialPort |
  Select-Object DeviceID,Name,PNPDeviceID,Status |
  Format-Table -AutoSize
```

ESP32-S3 原生 USB 通常显示为 `USB 串行设备`，本次设备显示为 `COM17`，PNP VID 为 `303A`。端口号可能因重新插拔变化，不能永久写死为 COM17。

3. 确认 Espressif Python 环境和 esptool：

```powershell
$idfPython = 'C:\Espressif\tools\python\v6.0.2\venv\Scripts\python.exe'
& $idfPython --version
& $idfPython -m esptool version
& $idfPython -m serial.tools.miniterm --help
```

若上述路径不同，以本机 Espressif 安装目录中实际存在的 `python.exe` 为准。不要用 WindowsApps 的占位 `python.exe` 代替 IDF 环境。

## 用串口监视器读取 IP

### 交互读取

保持设备 USB 供电并按一次 `BOOT/KEY` 唤醒，然后运行：

```powershell
& $idfPython -m serial.tools.miniterm COM17 115200
```

看到下面的提示后，串口已经打开：

```text
--- Miniterm on COM17  115200,8,N,1 ---
--- Quit: Ctrl+] | Menu: Ctrl+T followed by Ctrl+H ---
```

在设备上按一次复位键或电源键，等待启动过程。退出监视器使用 `Ctrl+]`，不要用 `Ctrl+C` 强制终止仍占用串口的进程。

### 需要保存日志时

在 IDF 工程环境中可使用 monitor 并保存输出：

```powershell
idf.py monitor -p COM17 -b 115200 2>&1 | Tee-Object -FilePath .\photopainter-boot.log
```

如果只使用 miniterm，可在终端窗口中复制启动日志到 `photopainter-boot.log`。日志至少应包含芯片 MAC、固件版本、Wi-Fi 状态、`sta ip`、HTTP server 状态和时间。

保存日志后，可用下面的 PowerShell 片段自动提取最后一条 STA IPv4 地址并验证网页：

```powershell
$staLine = Select-String -Path .\photopainter-boot.log -Pattern 'sta ip:' |
  Select-Object -Last 1
if (-not $staLine) {
  throw '日志没有 sta ip：设备可能仍在 AP 配网，或尚未取得 DHCP 地址。'
}
$photoIp = [regex]::Match($staLine.Line, '\b(?:\d{1,3}\.){3}\d{1,3}\b').Value
if (-not $photoIp) { throw 'sta ip 日志行中没有 IPv4 地址。' }
Write-Host "PhotoPainter URL: http://$photoIp/"
curl.exe --noproxy "*" --connect-timeout 5 --max-time 10 "http://$photoIp/api/system-info"
```

若日志中同时出现网关地址，正则只在包含 `sta ip:` 的行上执行，不会把网关误当成设备地址。

## 判断 AP 还是局域网 STA

串口输出中出现以下文字时，设备还没有加入家庭网络：

```text
No WiFi credentials found - Starting AP mode
```

此时用手机或电脑连接屏幕上显示的 `PhotoFrame - XXXXX` 网络，在设备配网页面选择家庭 **2.4 GHz** Wi-Fi 并保存。AP 网关地址不要从经验值推断；以屏幕、串口或设备网页实际显示为准。

配网成功后，日志应出现类似以下完整链路：

```text
wifi:connected with Zhong, ...
esp_netif_handlers: sta ip: 192.168.1.137, mask: 255.255.255.0, gw: 192.168.1.1
wifi_manager: got ip:192.168.1.137
http_server: HTTP server started
main: Web interface available at: http://192.168.1.137
```

本次新微雪设备的实际记录为：

```text
target: waveshare_photopainter_73
firmware: esp32-photoframe v2.18.0
chip: ESP32-S3 rev 0.2
mac: a4:cb:8f:da:a1:dc
serial: COM17
sta_ip: 192.168.1.137
ssid: Zhong
```

MAC 是设备身份，不是网页地址；`PhotoFrame - AA1DC` 是配网 AP 的 SSID，不是 STA IP。

## 不依赖 photoframe.local 的网页验证

在同一个家庭局域网的电脑上，直接使用串口日志中的 IPv4 地址：

```powershell
$photoIp = '192.168.1.137'
curl.exe --noproxy "*" --connect-timeout 5 --max-time 10 "http://$photoIp/"
curl.exe --noproxy "*" --connect-timeout 5 --max-time 10 "http://$photoIp/api/system-info"
Test-Connection -ComputerName $photoIp -Count 3
```

成功时网页返回 `HTTP/1.1 200 OK`，系统信息至少应确认：

```json
{
  "board_name": "waveshare_photopainter_73",
  "width": 800,
  "height": 480,
  "version": "v2.18.0",
  "project_name": "esp32-photoframe"
}
```

本次 `192.168.1.137` 已实测返回上述板型、800x480、v2.18.0 和 `HTTP 200`。因此浏览器应打开：

```text
http://192.168.1.137/
```

不需要登录 `photoframe.local`。若 `photoframe.local` 能解析，它只是同一个设备的别名；解析失败时继续使用 IPv4 地址。

## 与 310B 相册服务器连接

确认 PhotoPainter 已经处于 STA 局域网状态后，再在设备网页中配置 Case7 的图片 URL。310B 当前服务地址为 `http://192.168.1.135:7860/`。先从 Case7 设备页注册并取得设备 ID，再把下列 URL 中的 `<device_id>` 替换为实际值：

```text
http://192.168.1.135:7860/api/devices/<device_id>/photoframe
```

在 310B 上先验证服务：

```bash
curl http://127.0.0.1:7860/api/health
curl http://127.0.0.1:7860/api/models
curl http://127.0.0.1:7860/api/index/stats
```

然后在 PhotoPainter 网页的 URL Rotation/图片 URL 设置中保存服务器 URL。服务器 URL 必须是 PhotoPainter 所在局域网可达的 310B 地址，不能填写 `127.0.0.1`，也不能填写电脑的串口号。

## IP 变化与故障排查

| 现象 | 检查 | 处理 |
| --- | --- | --- |
| `photoframe.local` 打不开 | 先看串口 `sta ip` | 直接访问 `http://<sta_ip>/`；mDNS 不是必需项 |
| 串口没有新日志 | 设备可能进入休眠 | 按一次 `BOOT/KEY` 唤醒，再复位并重新打开监视器 |
| `COM17` 不存在 | USB 重新枚举 | 重新运行串口枚举命令，使用当前 `VID_303A` 端口 |
| 只有 `No WiFi credentials...` | 设备仍在 AP 模式 | 连接 `PhotoFrame - XXXXX`，完成 2.4 GHz 配网 |
| 有 `sta ip` 但网页超时 | 电脑不在同一网段、端口被占用或设备休眠 | 检查电脑 IPv4、按键唤醒，再执行 `curl` |
| `/api/system-info` 的板型不是 Waveshare | 固件与硬件镜像不匹配 | 停止配置，重新核对固件文件名和 SHA-256 |

不要把旧 E1002 的 `192.168.1.117`、历史 `COM13` 或任意 ARP 条目当成当前微雪地址。每次换路由器、重置 Wi-Fi 或重新刷写后，都重新读取一次 `sta ip` 并记录时间。

## 本次刷写证据

本次操作只针对新的 Waveshare PhotoPainter，不涉及 E1002：

```text
release: v2.18.0
asset: photoframe-firmware-waveshare_photopainter_73-merged.bin
asset_sha256: 41a680d59ae65f37ef581fd66568a988fd0e64469617651b1a1ec98e77fd30b3
flash_tool: esptool v5.3.1
flash_command: write-flash --chip esp32s3 --flash-size 16MB 0x0 <asset>
flash_result: Hash of data verified
original_firmware_observed: xiaozhi 2.0.1 (ESP-IDF v5.5.3)
boot_log: C:\Users\zhoux\Downloads\waveshare_photopainter_boot.log
```

完整 Flash 备份因长串口读取中断未完成；不要把失败的部分文件当作可回滚镜像。刷写成功、STA 配网成功和真实电子纸刷新是三个独立验收项。

## 参考资料

- [ESP32 PhotoFrame API](https://github.com/aitjcize/esp32-photoframe/blob/main/docs/API.md)
- [PhotoFrame Releases](https://github.com/aitjcize/esp32-photoframe/releases)
- [Waveshare ESP32-S3-PhotoPainter](https://www.waveshare.com/wiki/ESP32-S3-PhotoPainter)
