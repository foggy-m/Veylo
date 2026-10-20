# Veylo — Video Downloader

Video, your way. 好内容，随心留存。

Veylo 是 Windows 上的非官方浏览器下载助手，支持 YouTube、X 和哔哩哔哩。浏览器界面版本 **2.4.2**，通过本机组件调用用户自行安装的 FluentYTDL / Bili23-Downloader；项目不提供云端下载服务，也不打包第三方下载器。

公开版通过**浏览器工具栏弹窗**操作，快捷键为 **Alt+Shift+V**。为隔离网页脚本，公开版不注入网页悬浮面板，不申请站点访问权限，不向网页开放扩展资源。代码中的旧 content 面板仅作为历史回归夹具保留，manifest 不加载它。

> 本仓库发布源码，不包含预编译下载器或本机组件。验证结果与覆盖范围见 [发布检查记录](PUBLICATION.md)。

## 功能

- 单视频、YouTube 播放列表、B 站分 P / 合集下载，任务进度、取消、重试和本机历史。
- 视频、原音轨、MP3 / M4A 转换；原音轨不主动重新编码。
- YouTube 字幕、可选 SponsorBlock、时间片段、封面、章节、防重复下载、限速和并发分片。
- B 站外置 SRT 字幕及可选 ASS 弹幕。
- 选择本机保存位置，打开任务所属媒体或文件夹。

具体能力取决于平台提供的内容、账号权限及用户安装的内核。项目不绕过平台访问控制。只下载你有权保存的内容。

## 环境

- Windows 10 / 11；Chrome、Edge 或兼容 Chromium 116+ 浏览器。
- 可用的 Python 3.10+ 命令（实际本地验证为 Python 3.11）。
- Windows .NET Framework 4.x C# 编译器（构建脚本会检查是否存在）。
- YouTube / X：自行安装 FluentYTDL 完整便携版，包含 `bin/yt-dlp/yt-dlp.exe`、`bin/ffmpeg/ffmpeg.exe`；原音轨还需要同目录 `ffprobe.exe`。
- B 站：自行安装 Bili23-Downloader 便携版，包含 `Bili23.exe`、`runtime/python.exe`、`bundle/ffmpeg.exe` 和 `script/util`。当前接口适配基于 Bili23 2.14.0，其他版本需要自行验证。

下载器、运行时和 FFmpeg 的安全更新由其上游提供。本次源码检查未覆盖它们的全部实现及已知漏洞版本。

## 首次安装

1. 将源码放在可写的普通本机目录，例如 `C:\Apps\Veylo`，不要从网络共享或目录链接启动。
2. YouTube / X：可将 FluentYTDL 便携版放在桌面的 `FluentYTDL-3.7.6-win64-full`，或在 `native/fluent_paths.json` 中指定实际位置：

   ```json
   { "fluent_root": "C:\\Apps\\FluentYTDL" }
   ```

   这是你本机的私有配置，已被 `.gitignore` 排除。桥接也支持桌面 `Applications` 下唯一匹配的 FluentYTDL 完整便携版。
3. 运行根目录的 `安装浏览器助手.bat`。它会在缺少 EXE 时从源码构建 YouTube / X 组件，然后自检并注册。
4. 首次使用 B 站时，在项目根目录打开终端执行：

   ```powershell
   .\bilibili\安装Bili下载助手.bat --bili-dir "C:\Apps\Bili23-Downloader"
   ```

   该脚本构建 B 站组件，然后检测、自检并注册。B 站音频优先读取同一份 `native/fluent_paths.json`；未配置时再检查桌面的 FluentYTDL 目录或桌面 `Applications`。显式配置只接受本机普通路径，拒绝目录链接和重解析点。找不到所需编码器或 ffprobe 时会给出错误。
5. 打开 `chrome://extensions/` 或 `edge://extensions/`，开启开发者模式，加载项目中的 `extension` 目录。
6. 点击工具栏 Veylo 图标，检查本机连接，再提交下载。连接成功只证明组件和引擎可用，不代表真实平台下载已经验证。

本机组件兼容名称仍为 `com.shiguang.fluentytdl_bridge` / `com.shiguang.bili_bridge`，固定扩展 ID 为 `ikaahmfclfkamdgikchmjnoalpklkddk`。manifest 的 `key` 是用于固定身份的公钥，可以公开。

安装器只写当前用户的注册项。遇到既有其他路径或未知归属注册会停止，不会强行覆盖。如果已经安装旧版，请先记录原有安装位置，再按明确的迁移方案更新；不要通过删除未知注册来绕过检查。升级后重新加载扩展，并刷新此前打开的平台网页，清除旧面板。

## 隐私与公开范围

此仓库使用独立文件清单创建，包含源码、测试、静态素材和第三方图标授权。没有发布 Cookie、浏览器配置文件、下载历史、任务日志、个人绝对路径、旧归档或预编译 EXE；也没有把本地使用记录导入 Git 历史。

扩展偏好和任务记录保存在本机。下载内核可以使用你在其软件中保存的登录状态；这不表示 Cookie 可以公开。平台请求、账号登录和可选 SponsorBlock 请求仍会连接相应服务。遇到问题时不要把完整配置、Cookie 或日志原文贴进公开 issue。

安全检查结果及限制见 [发布检查记录](PUBLICATION.md)。

## 开发验证

无需 npm 安装即可运行基础离线检查（需要 Node.js 和 Python）：

```powershell
python -B tools/check.py
```

Windows 原生桥接及本地合成媒体验证需要独立测试环境，不属于上述基础检查：

```powershell
$env:VEYLO_TEST_FLUENT_DIR = "C:\Apps\FluentYTDL"
$env:VEYLO_TEST_BILI_DIR = "C:\Apps\Bili23-Downloader"
python -B tools/check_native.py
```

`check_native.py` 串行运行两套完整测试，启动前通过 Windows Job Object 设置并回读进程数和内存限制，每套最多运行 8 分钟；失败或结束后清理所属进程树。日志保存在已忽略的 `.test-results/native/`。这些测试使用隔离任务、mock 和本地合成媒体，不登录账号、不进行真实平台下载。需要上面指定的已有工具；不随仓库提供。UI 的 DOM 测试不等于真实浏览器视觉检查。

## 第三方素材与许可

Lucide 图标的固定来源记录和授权保留在 `extension/third_party/lucide/SOURCE.json` 与 `LICENSE`。本仓库暂未为原创代码添加开源许可证。FluentYTDL、Bili23、yt-dlp 和 FFmpeg 由各自项目提供，不在本仓库中再分发。
