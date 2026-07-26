# 备份下载与恢复

## 从 Fluidd 下载

1. 打开 Fluidd 左侧“云端备份”。
2. 在“备份记录”中找到状态为“已上传”的任务。
3. 点击下载按钮。
4. Moonraker 检查本地归档；本地归档不存在时，从百度 bypy 目录逐文件下载。
5. 文件清单、大小和 SHA-256 全部通过后才会生成 `.tar.gz`。

如下载按钮不可用，常见原因是：任务失败、历史记录不完整、当前没有授权，或正在进行其他备份/下载。

## 在 Linux 上校验下载包

```bash
mkdir -p ~/config-restore-review
tar -xzf 20260727_000810_dc0bde4928b9.tar.gz \
  -C ~/config-restore-review
cd ~/config-restore-review
sha256sum -c SHA256SUMS
```

再阅读：

```bash
less '备份说明.txt'
less backup-manifest.json
```

## 安全恢复原则

下载功能故意不提供“一键覆盖”。恢复前：

1. 确认打印机不在打印或暂停状态。
2. 先备份当前 `printer_data/config`。
3. 在独立目录解压，不直接对 `config` 解压。
4. 比较需要恢复的具体文件，不覆盖与故障无关的新配置。
5. 特别检查 MCU 串口/CAN UUID、引脚、热敏电阻类型、加热功率、限位和宏中的物理坐标。
6. 完成 Klipper 语法检查后再重启。

## 选择性恢复示例

```bash
stamp=$(date +%Y%m%d_%H%M%S)
cp -a ~/printer_data/config \
  ~/printer_data/config-before-restore-$stamp

# 仅恢复已经对比确认的文件
cp ~/config-restore-review/config/printer.cfg \
  ~/printer_data/config/printer.cfg.restore-candidate
```

建议先把文件作为 `.restore-candidate` 放到配置目录，使用差异工具或 Fluidd 编辑器对比，确认后再替换目标文件。

## 安装器回滚与云备份恢复的区别

- `./install.sh uninstall` 恢复项目首次安装前的 Moonraker 组件和 Fluidd，用于卸载本项目。
- 云备份下载用于找回 Klipper/Moonraker 配置文件，不会自动安装或卸载项目。
