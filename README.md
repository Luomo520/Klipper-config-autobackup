# Klipper-config-autobackup

`Klipper-config-autobackup` 是集成在 Fluidd 中的 Klipper 配置云备份工具。备份由打印机 Linux 主机直接创建和上传，不经过 PC，也不需要长期运行桌面网盘客户端。

Current release: `v0.1.0-alpha`

## 主要功能

- Fluidd 左侧导航中的“云端备份”页面。
- 百度网盘 bypy 命令行授权，默认轻量方案，不运行 Chromium。
- 保留百度账号密码网页登录，可选安装 Playwright/Chromium。
- GitHub 私有仓库归档。
- 百度 bypy 模式按原目录结构逐文件上传，不分块、不只上传一个压缩包。
- 手动备份与定时/开机延时自动备份。
- 显示当前文件、已校验文件数、已校验字节和总字节。
- 每份备份生成 `备份说明.txt`、`backup-manifest.json` 和 `SHA256SUMS`。
- 历史备份下载：下载前验证清单和 SHA-256，不会自动覆盖当前配置。
- 安装、更新、卸载前自动备份，失败自动回滚。

## 快速安装

SSH 进入打印机 Linux 主机：

```bash
cd ~
git clone --depth 1 https://github.com/Luomo520/Klipper-config-autobackup.git
cd Klipper-config-autobackup
chmod +x install.sh
./install.sh install
```

安装器默认安装轻量的 `bypy 1.8.9`。如需百度账号密码登录：

```bash
./install.sh install --with-web-login
```

`--with-web-login` 会安装 Playwright 和 Chromium，占用空间明显大于 bypy；普通用户建议使用默认 bypy 授权。

## 管理命令

```bash
# 检查路径、组件哈希和 API 状态
./install.sh status

# 使用 git 下载最新代码，备份后更新
./install.sh update

# 备份当前状态，恢复首次安装前的组件和 Fluidd
./install.sh uninstall
```

卸载不会删除 `printer_data/cloud_backup` 中的历史备份、授权信息和安装器回滚资料。

## Fluidd 兼容策略

安装器不设置 Fluidd 版本白名单，不会因版本号不同拒绝安装。安装时会先完整备份当前 Fluidd，再原子替换为本仓库内已验证构建。这意味着：

- 任意 Fluidd 版本都可尝试安装。
- 安装后的实际前端为项目附带的完整构建，不是将新 chunk 混入旧目录。
- 如启动检查失败，安装器自动恢复安装前版本。

## 文档

- [完整安装教程](docs/INSTALL.md)
- [功能详解](docs/FEATURES.md)
- [百度网盘授权](docs/BAIDU.md)
- [备份下载与恢复](docs/RESTORE.md)
- [故障排查](docs/TROUBLESHOOTING.md)
- [Moonraker API](docs/API.md)
- [源码与重新构建](docs/SOURCE.md)
- [更新记录](CHANGELOG.md)

## 安全边界

- 不上传、记录或返回密码、bypy token、GitHub token 或 SSH 密钥。
- 前端只能选择 Moonraker 允许的备份根，不能提交任意 Linux 路径。
- 打印进行中安装器拒绝替换文件和重启 Moonraker。
- 下载只生成受控归档，不会自动恢复或覆盖 `printer_data/config`。

## 许可证

GPL-3.0-only。详见 [LICENSE](LICENSE)。
