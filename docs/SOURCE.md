# 源码与重新构建

## 载荷来源

- Moonraker 组件源码：`source/moonraker`。
- Fluidd 修改文件：`source/fluidd`。
- 安装载荷：`payload`。
- Fluidd 构建基线：`fluidd-core/fluidd` `1.37.2`。
- 项目版本：`0.1.0-alpha`。

安装器不限制目标 Fluidd 版本，但为了可重现发布包，必须记录用于构建载荷的源码基线。

## 重新构建 Fluidd

1. 取得 Fluidd 1.37.2 源码。
2. 将 `source/fluidd` 中的文件按相同相对路径覆盖到 Fluidd 源码树。
3. 使用项目锁定的 pnpm 环境安装依赖。
4. 执行完整验证：

```bash
pnpm install --frozen-lockfile
pnpm run type-check
pnpm run test:unit
pnpm run lint
pnpm run build
pnpm run circular-check
```

5. 删除旧发布目录后，将新 `dist` 单独打包：

```bash
tar -czf fluidd-dist.tar.gz -C dist .
```

6. 替换 `payload/fluidd/fluidd-dist.tar.gz`，更新 `payload/SHA256SUMS`。

不要将新 `dist/assets` 覆盖到旧构建中，否则旧哈希 chunk 可能留存。

## 验证 Moonraker 组件

将 `source/moonraker/cloud_backup.py` 和 `cloud_backup_web.py` 放入 Moonraker 源码树的 `moonraker/components`，将测试放入 `tests`，然后运行对应单元测试。

发布时 `source/moonraker` 与 `payload/moonraker` 的两个组件必须字节相同。

## 许可证

Moonraker、Fluidd 和本项目代码按 GPL-3.0-only 发布。完整文本见根目录 `LICENSE`。
