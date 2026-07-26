# 故障排查

## 安装器找不到 Fluidd

```text
Fluidd web root was not found
```

查找实际 `index.html`：

```bash
find ~ /usr/share /var/www -maxdepth 3 -name index.html 2>/dev/null
```

然后指定：

```bash
KLIPPER_BACKUP_FLUIDD_ROOT=/actual/fluidd/path ./install.sh install
```

安装器不检查 Fluidd 版本号，该错误只表示未找到实际 Web 根目录。

## 无法查询 `print_stats`

先检查 Moonraker：

```bash
curl -sS http://127.0.0.1:7125/server/info
curl -sS 'http://127.0.0.1:7125/printer/objects/query?print_stats'
```

安装器为了避免打印中重启，默认会停止。仅在你已通过其他方式确认设备空闲且 Moonraker 必须离线安装时，使用：

```bash
KLIPPER_BACKUP_ALLOW_OFFLINE=1 ./install.sh install
```

## `python3-venv` 缺失

Debian/Ubuntu：

```bash
sudo apt update
sudo apt install python3-venv
```

也可先跳过 bypy：

```bash
./install.sh install --skip-bypy
```

但之后必须在 `printer_data/cloud_backup/bypy-env` 安装 bypy，命令行授权才会可用。

## Fluidd 出现空白页

```bash
./install.sh status
find ~/fluidd/assets -maxdepth 1 -name 'CloudBackup-*.js'
chmod -R a+rX ~/fluidd
```

如 Fluidd 路径不是 `~/fluidd`，替换为实际路径。浏览器强制刷新，并检查 Nginx 请求 JavaScript 时返回的是 JavaScript，不是回退的 `index.html`。

安装器启动验证失败时会自动回滚。手动卸载：

```bash
./install.sh uninstall
```

## bypy 未安装

```bash
~/printer_data/cloud_backup/bypy-env/bin/bypy --version
```

如文件不存在，重新执行：

```bash
./install.sh install
```

## bypy 授权失效

Fluidd 会显示授权过期。点击解除授权，然后重新执行官方链接授权。不要把 `bypy.json` 复制到论坛或 Issue。

## 上传失败或进度不增长

1. 查看 Fluidd 中的当前文件和错误文本。
2. 查看 `~/printer_data/logs/moonraker.log`，但不要公开完整日志前忘记删除私密信息。
3. 确认网络可访问百度 API。
4. 确认目标仍在“我的应用数据/bypy”内。
5. 大文件需要更长时间；字节进度只在整个文件通过远端大小校验后增长。

## 下载失败

- 确认历史任务为 `success`。
- 确认百度 bypy 授权有效。
- 检查云端任务目录中只有一个 `backup-manifest.json`。
- 不要手动编辑云端目录内的清单或校验表，否则下载会拒绝发布归档。

## 收集诊断信息

```bash
./install.sh status
curl -sS http://127.0.0.1:7125/server/cloud_backup/status
systemctl status moonraker --no-pager
tail -n 200 ~/printer_data/logs/moonraker.log
```

提交 Issue 前删除用户名、密码、token、授权码、Cookie、设备内网地址和私有配置内容。
