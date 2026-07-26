# 完整安装教程

## 1. 安装前提

需要：

- 运行 Linux 的 Klipper 上位机。
- Moonraker 和 Fluidd 已正常运行。
- `python3`、`python3-venv`、`git`、`curl`、`tar`、`sha256sum`。
- 当前 Linux 用户可写 Moonraker 组件目录、`moonraker.conf` 和 Fluidd Web 根目录。
- 打印机不处于 `printing` 或 `paused` 状态。

安装器会查询 `print_stats`。无法确认打印状态时默认停止，不会盲目重启服务。

## 2. 使用 Git 下载

```bash
cd ~
git clone --depth 1 https://github.com/Luomo520/Klipper-config-autobackup.git
cd Klipper-config-autobackup
chmod +x install.sh
```

不建议下载不明来源的二次打包。安装前可先核对载荷：

```bash
cd payload
sha256sum -c SHA256SUMS
cd ..
```

## 3. 执行默认安装

```bash
./install.sh install
```

默认行为：

1. 检查路径、写权限、载荷 SHA-256 和打印状态。
2. 首次安装创建永久卸载基线。
3. 备份完整 `printer_data/config`、Moonraker 目标组件和完整 Fluidd。
4. 在 `printer_data/cloud_backup/bypy-env` 安装 `bypy 1.8.9`。
5. 在临时目录解压 Fluidd，检查 `index.html` 和 `CloudBackup-*.js`。
6. 使用 Moonraker Python 编译检查两个组件。
7. 仅在缺失时追加 `[cloud_backup]` 默认配置，已有配置不会被重置。
8. 原子替换组件和完整 Fluidd 目录。
9. 重启 Moonraker，等待 `/server/cloud_backup/status` 可用。
10. 失败时恢复变更前文件并尝试重启原服务。

## 4. 可选账号密码登录

```bash
./install.sh install --with-web-login
```

该选项在 Moonraker Python 环境安装 Playwright，并下载 Chromium。仅在明确需要“账号密码登录”时使用；默认 bypy 不需要 Chromium。

## 5. 自定义安装路径

安装器不根据 Fluidd 版本号做限制，但必须能找到实际路径。非标准安装可显式指定：

```bash
KLIPPER_BACKUP_PRINTER_DATA=/home/pi/printer_data \
KLIPPER_BACKUP_MOONRAKER_ROOT=/home/pi/moonraker \
KLIPPER_BACKUP_MOONRAKER_CONFIG=/home/pi/printer_data/config/moonraker.conf \
KLIPPER_BACKUP_FLUIDD_ROOT=/var/www/fluidd \
KLIPPER_BACKUP_MOONRAKER_PYTHON=/home/pi/moonraker-env/bin/python \
./install.sh install
```

如目标目录属于 root，先确认实际部署方式和权限；安装器不会自动放宽整个系统目录权限。

## 6. 安装后授权

1. 刷新 Fluidd。
2. 打开左侧“云端备份”。
3. 选择百度网盘与“命令行授权”。
4. 保存配置，点击开始授权。
5. 在百度官方页面确认，将一次性授权码填回 Fluidd。
6. 填写至少 10 个字符的备份原因，执行第一次手动备份。

百度 bypy 只能访问“我的应用数据/bypy”。默认 `/3D打印机备份` 实际显示为：

```text
我的应用数据/bypy/3D打印机备份
```

## 7. 更新

```bash
cd ~/Klipper-config-autobackup
./install.sh update
```

`update` 会拒绝覆盖未提交的本地仓库修改，执行 `git pull --ff-only`，然后再走一次完整备份安装流程。

## 8. 卸载

```bash
cd ~/Klipper-config-autobackup
./install.sh uninstall
```

卸载会：

- 先创建 `before-uninstall` 事务备份。
- 恢复首次安装前的 Moonraker 组件和完整 Fluidd。
- 只恢复安装前 `[cloud_backup]` 配置段，不回退其他后续配置修改。
- 保留 `printer_data/cloud_backup` 下的备份、授权、基线和事务备份。

如首次安装基线不完整，卸载器会拒绝猜测原始文件。

## 9. 备份位置

```text
printer_data/cloud_backup/installer/
  baseline/                 # 首次安装前永久基线
  backups/<time>-<stage>/   # 更新和卸载前备份
  state                     # 安装器状态，权限 0600
```

每个备份包含 `BACKUP_INFO.txt` 和 `SHA256SUMS`。
