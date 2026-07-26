# 功能详解

## 系统结构

```text
Fluidd 云端备份页
        |
        | Moonraker HTTP / WebSocket API
        v
cloud_backup Moonraker 组件
        |
        +-- 创建本地一致性快照
        +-- 生成说明、清单和 SHA-256
        +-- bypy 逐文件上传百度网盘
        +-- Playwright 可选网页上传
        +-- GitHub Contents API 归档
        +-- 验证后生成下载归档
```

Fluidd 不直接读取 Linux 文件或保存云端凭据。文件、凭据、调度和上传由 Moonraker 组件管理。

## 备份内容

默认只允许 Moonraker 文件管理器的 `config` 根，对应：

```text
~/printer_data/config
```

快照会：

- 保持原文件夹结构。
- 记录文件大小、SHA-256 和权限模式。
- 默认跳过 `.git` 和 `__pycache__`。
- 不跟随符号链接到根目录外。符号链接会被记录成 `.symlink.txt`。
- 记录空目录、跳过项目和符号链接目标。

## 人类可读的百度备份

bypy 模式不把配置拆成分块，也不只上传一个 tar 包。每次任务创建新目录：

```text
3D打印机备份/backups/YYYY/YYYY-MM/YYYY-MM-DD/<timestamp>_<job>/
  备份说明.txt
  SHA256SUMS
  backup-manifest.json
  config/
    printer.cfg
    moonraker.conf
    saved_variables.cfg
    ...
```

在百度网盘中可直接进入 `config` 查看 `.cfg` 和 `.conf` 文件。

## 三个校验文件

### `备份说明.txt`

包含本地时间、任务 ID、备份原因、来源根、文件数、校验方式和恢复用途。

### `SHA256SUMS`

标准批量 SHA-256 表，可在 Linux 中执行：

```bash
sha256sum -c SHA256SUMS
```

### `backup-manifest.json`

机器可读清单，记录所有文件的路径、大小和 SHA-256。bypy 上传时它排在最后；只有所有其他文件通过远端大小验证后才会上传。因此该文件是完整备份标志。

## 真实进度

Fluidd 显示：

- `current_file`：正在上传或验证的相对路径。
- `uploaded_files / upload_total_files`：已通过远端大小验证的文件数。
- `uploaded_bytes / upload_total_bytes`：已校验文件的总字节数。
- `upload_progress`：上述已校验字节占比。

数值只在对应文件上传并通过 `bypy meta` 大小检查后增长，不是按时间估算的假进度。

## 手动备份

手动备份需要 10-500 个字符的原因。原因同时记录到说明文件和历史列表，用于区分“修改挤出机参数前”、“更换热端后”等节点。

启用自动备份后，手动按钮仍然保留。

## 自动备份

### 按天数间隔

从最近一次成功的手动或自动备份起计算。到期后创建新任务。

### 每次开机后

Moonraker 启动后等待配置的分钟数，当前进程只创建一次开机任务。失败任务保留重试资格。

认证不可用、其他备份运行中或系统忙时，调度器会等待而不是并发上传。

## 备份下载

成功历史记录显示下载按钮。

- 本地保留归档存在时，校验 SHA-256 后发布下载。
- 本地归档已过期但 bypy 可读目录存在时，从百度逐文件下载。
- 对 `backup-manifest.json` 和 `SHA256SUMS` 双重校验。
- 校验成功后在受控目录生成 `.tar.gz`。
- 临时下载包保留 24 小时，并限制数量。

下载和恢复是两个不同动作：下载不会向打印机当前配置写入任何文件。

## 本地保留与资源占用

- 闲置时只有 Moonraker 内的调度器和低频状态维护，不启动 Chromium。
- bypy 仅在授权、上传、远端校验或下载时启动子进程。
- 账号密码模式仅在登录或网页上传时启动 Chromium。
- `retain_local` 控制本地归档数量，默认 5。

## GitHub 备份目标

GitHub 模式使用 Contents API 将 tar 归档写入指定仓库、分支和目录。建议使用私有仓库和仅限该仓库 Contents 读写的 fine-grained token。单文件上限为 100 MiB。

“将打印机配置备份到 GitHub”与“在 GitHub 上发布本项目源码”互相独立。
