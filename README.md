# Veylo — Video Downloader

Video, your way. 好内容，随心留存。

Veylo 是 Windows 上的非官方浏览器下载助手，支持 YouTube、X 和哔哩哔哩。浏览器扩展与下载组件在本机工作。

## 安装与使用

**[下载 Veylo v2.4.7 Windows x64 安装器](https://github.com/foggy-m/Veylo/releases/download/v2.4.7/Veylo-2.4.7-Setup-x64.exe)**

适用于 Windows 10/11 x64，以及 Chrome、Edge、Tabbit 等基于 Chromium 116 或更新版本的浏览器。首次安装需要访问 GitHub，下载量约 246 MiB，建议预留 2 GB 磁盘空间。 [发布说明](https://github.com/foggy-m/Veylo/releases/tag/v2.4.7) · [SHA256 校验](https://github.com/foggy-m/Veylo/releases/download/v2.4.7/SHA256SUMS.txt)

1. 双击 `Veylo-2.4.7-Setup-x64.exe`。可直接点「安装」使用默认位置，也可点「浏览」或输入完整路径，例如 `D:\Veylo`。自选位置可以是新文件夹、空文件夹或 Setup 管理的 Veylo 安装目录。安装器会自动下载、校验并解压组件，再按实际路径连接浏览器。
2. 安装完成后选择浏览器，点击「打开扩展管理页」。启用「开发者模式」，选择「加载已解压的扩展程序」，粘贴安装器已复制的扩展目录并确认。每个浏览器加载一次。若未进入管理页，可在 Chrome 或 Tabbit 输入 `chrome://extensions/`，在 Edge 输入 `edge://extensions/`。
3. 打开 YouTube、X 或哔哩哔哩的视频页，点击工具栏里的 Veylo，或按 **Alt+Shift+V**，选择「下载视频」。在「任务记录」看到「已完成」后可打开文件。默认保存目录是安装目录下的 `Downloads`，也可以在扩展内另选位置。

如果之前从源码加载过旧 Veylo，请先备份需要保留的扩展偏好和任务记录，再在扩展管理页移除旧条目，按上面的步骤加载新版目录。需要账号权限时，可在安装完成页点「打开下载器登录」，用自己的账号登录。

## v2.4.7 安装目录

默认安装位置为 `%LOCALAPPDATA%\Veylo`。选择其他位置时，扩展、下载组件和开始菜单入口会随所选路径配置。Setup 会记住成功安装的目录。从旧目录切换时，新位置会独立安装，旧目录里的文件继续存放在原处。浏览器已加载过旧目录时，请用安装器复制的新扩展路径重新加载。

## v2.4.6 安装器更新

修复 Windows 高缩放下安装说明、下载状态和按钮文字被截断的问题。长状态会自动换行，窄窗口内可以滚动查看详细信息。v2.4.5 用户直接运行新版安装器即可更新。

## v2.4.5 更新

- 新增 Windows x64 安装器，以及开始菜单里的「Veylo 安装与更新」。
- 修复首次使用时缺少 FluentYTDL 配置导致的桥接报错。B 站游客下载会自动获取播放初始化信息。
- 新增兼容组件检查、yt-dlp 独立更新与回退。新版桥接版本为 YouTube / X 1.7.3 和 B 站 1.5.3。

全新 Windows 验收覆盖安装、浏览器连接、组件更新和回退。X 和 B 站的游客样本下载成功，YouTube 样本在验收机上遇到了站点登录校验。 [验收记录](https://github.com/foggy-m/Veylo/actions/runs/45001860087) · [验收报告](https://github.com/foggy-m/Veylo/releases/download/v2.4.5/clean-install-report.json) · [历史更新记录](CHANGELOG.md)

## 更新下载组件

开始菜单里的「Veylo 安装与更新」提供「组件更新」。可以手动检查一次，也可选择每次打开管理器时自动更新。首次安装使用已验证的 FluentYTDL 3.7.6 和 Bili23 2.14.0。

- yt-dlp 可以从其官方稳定发布独立更新。安装器核对 SHA256，并检查 yt-dlp、Deno、FFmpeg 和 ffprobe 能否按现有方式运行。遇到兼容问题可在同一菜单恢复随包 yt-dlp。
- FluentYTDL 与 Bili23 的官方新版本会显示在检查结果里。通过 Veylo 验收且适配当前桥接版本的组件会出现在发布页的兼容清单里，管理器会校验并在单独目录准备新版。检查失败时会继续使用现有组件。
- Bili23 2.21.0 的官方便携包缺少当前桥接使用的 Python 模块，仍使用已验证的 2.14.0。Bili23 配置文件的格式也属于兼容验证范围。

发布用的安装器和兼容清单都由 Windows 验收任务产出，完成验证后一起放在 GitHub 发布附件中。

## 功能

- 单视频、YouTube 播放列表、B 站分 P / 合集下载，任务进度、实时下载速率、取消、重试和本机历史。
- 视频、原音轨、MP3 / M4A 转换；原音轨不主动重新编码。
- YouTube 字幕、可选 SponsorBlock、时间片段、封面、章节、防重复下载、限速和并发分片。
- B 站外置 SRT 字幕及可选 ASS 弹幕。
- 选择本机保存位置，打开任务所属媒体或文件夹。

具体能力取决于平台提供的内容、账号权限及用户安装的内核。项目不绕过平台访问控制。只下载你有权保存的内容。

## 从源码构建

在 Windows x64 上使用 Git 检出仓库，准备 Python 3.10+ 和 Windows .NET Framework 4.x C# 编译器。在仓库根目录运行以下命令构建安装器。

```powershell
python -B setup/build.py
```

安装器、SHA256 校验文件和组件兼容清单会写入 `dist/`。

<details>
<summary>从源码手动安装和注册本机组件</summary>

使用手动安装脚本时，请先准备对应的官方便携版。

- YouTube / X：自行安装 FluentYTDL 完整便携版，包含 `bin/yt-dlp/yt-dlp.exe`、`bin/ffmpeg/ffmpeg.exe`；原音轨还需要同目录 `ffprobe.exe`。
- B 站：自行安装 Bili23-Downloader 便携版，包含 `Bili23.exe`、`runtime/python.exe`、`bundle/ffmpeg.exe` 和 `script/util`。当前接口适配基于 Bili23 2.14.0，其他版本需要自行验证。

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
6. 点击工具栏 Veylo 图标，检查本机连接，再提交一个下载任务验证。

本机组件名称为 `com.veylo.youtube_x` / `com.veylo.bilibili`，固定扩展 ID 为 `ikaahmfclfkamdgikchmjnoalpklkddk`。manifest 的 `key` 是用于固定身份的公钥，可以公开。v2.4.5 使用新的注册名称，与旧版 `com.shiguang.*` 注册分开。

手动安装脚本只写入当前用户的注册项。遇到其他路径或未知归属的注册时，请核对原安装位置。更新源码版时，等现有任务结束，再用 `native/build.bat` 和 `bilibili/native/build.bat` 重新编译桥接组件，运行相应的安装脚本并重新加载扩展。

</details>

## 隐私与公开范围

Git 仓库包含源码、测试、静态素材和第三方图标授权。Windows 安装器由验收任务构建，发布在 GitHub Releases。公开版把下载操作限制在扩展的工具栏弹窗，网页浮层源码仅用于历史回归测试。

扩展偏好、任务记录和账号资料保存在本机。下载内核可以使用你在对应下载器中保存的登录状态。平台请求、账号登录和可选 SponsorBlock 请求仍会连接相应服务。公开反馈前，请移除配置和日志中的 Cookie、账号信息和个人路径。 [v2.4.4 历史源码安全检查](PUBLICATION.md)

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

速率与分段传输回归可分别运行 `python -B tests/download_speed_test.py` 和 `python -B tests/transfer_range_test.py`。后者需要 `httpx`，或通过上述 `VEYLO_TEST_BILI_DIR` 使用已有 Bili23 的依赖；网络请求仅连接本机测试服务器。

## 第三方素材与许可

Lucide 图标的固定来源记录和授权保留在 `extension/third_party/lucide/SOURCE.json` 与 `LICENSE`。本仓库暂未为原创代码添加开源许可证。FluentYTDL、Bili23、yt-dlp 和 FFmpeg 由各自项目提供，不在本仓库中再分发。
