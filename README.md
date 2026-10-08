# StellaVita 盒子浏览器

> **给图谱的盒子插上连接电脑的网线，就可以直接运行这个软件，来打开图谱盒子的文件来传输。**

一个给「图谱（ToupTek）StellaVita 天文盒子」准备的 Windows 桌面文件浏览器：一根网线直连盒子，浏览并高速下载盒子里的文件（FITS 序列、图像、视频等）。

> *Plug an Ethernet cable from your ToupTek StellaVita box into your computer, then simply run this app to browse and transfer files from the box.*

## ✨ 特性

- 🔌 **网线直连**：盒子 RJ45 ↔ 电脑网口即可，无需路由器、无需配置 WiFi
- 🔍 **自动发现**：自动找到盒子（上次地址 → 10.0.10.1 → mDNS 直连搜索），不用记 IP；也兼容盒子自带 WiFi 热点模式
- 🚀 **并行下载**：全局并行连接池，单个文件夹也会自动多连接并行（实测约 39 MiB/s，明显快于盒子自带 WiFi；并行数可在界面调整）
- 🌲 目录树浏览 / 文件列表 / 多选下载 / 批量任务队列 / 失败重试 / 进度跟踪
- 🔒 **只读安全**：仅浏览和下载，不修改盒子上的任何数据

## 🚀 使用（免安装 exe）

1. 用网线把盒子的网口和电脑网口连起来（盒子开机即可）
2. 双击运行 `StellaVitaBrowser.exe`（从 Releases 页面下载）
3. 软件会自动找到盒子并连上 → 开始浏览、下载

也可以在软件里手动指定盒子地址（顶栏「地址」按钮）。

## 🐍 从源码运行

```bash
pip install -r requirements.txt
python stellavita_browser.py
```

要求：Python 3.9+；依赖 `impacket`、`customtkinter`（见 `requirements.txt`）。Windows 用户也可直接双击 `run.bat`。

## ⚙️ 原理说明

盒子本质是一台 Linux 单板计算机（树莓派 CM4），内置 Samba 文件共享（共享名 `AstroStation`，出厂账号 `as` / `astrostation`，均为官方公开文档中的默认值）。本工具通过 SMB 协议直接访问该共享：

- **网线直连时**，盒子和电脑两端都会自动获得 `169.254.x.x` 链路本地地址；软件通过 mDNS（`raspberrypi.local`）自动定位盒子；
- 连接成功后即可像本地文件夹一样浏览、多选、批量下载数据。

也就是说：**不用官方 App、不用连盒子热点、不用任何特殊模式 —— 插上网线就能用。**

## ⚠️ 说明

- 目前仅在 Windows 10/11 上测试过
- 单文件打包程序可能被杀毒软件误报（常见误判），可添加信任，或改用源码方式运行
- 本工具不修改盒子上的数据（只读浏览 / 下载）
- 使用的默认账号来自官方公开文档，仅用于访问你自己的设备

## 📄 License

MIT
