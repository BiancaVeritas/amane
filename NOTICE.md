# 署名与分发说明

本项目是 [Amane](https://github.com/sqzw-x/amane) 的修改版. 上游版权归 `sqzw-x/amane` 的作者所有, 依 [GNU General Public License v3.0](LICENSE) 授权.

## 代码来源

`hakureireimu123/v0.26.0` 分支以官方 v0.26.0 为基础, 由 `BiancaVeritas/amane` 的维护者修改并构建, 包含自有功能与站点来源. 不含其他第三方的贡献.

## AI 使用披露

本分支的修改由维护者借助 AI 编程工具完成. 按 [AI_POLICY.md](AI_POLICY.md) 的披露要求:

- 披露级别: **AI 参与编写**. 方案由维护者确定, 代码由 AI 生成后经维护者 review 与调整
- 修改内容沿用上游的质量要求, 由维护者负责

## 相对上游的改动

- 新增本文件
- 新增镜像构建工作流 `.github/workflows/docker-hakureireimu123.yml`
- `compose.yaml` 的镜像地址指向 `ghcr.io/biancaveritas/amane-hakureireimu123`
- `README.md` 增加分发说明

上述四项为分发侧的改动. 功能层面的改动清单见 [CHANGELOG.md](CHANGELOG.md).

## 与 custom 分支的关系

`hakureireimu123/*` 与 `custom/*` 是两条独立的开发线, 代码与镜像都不共用. 两者的差异不通过合并同步.

## 镜像

`ghcr.io/biancaveritas/amane-hakureireimu123` 由本仓库的 GitHub Actions 构建, 与 `custom/*` 分支构建的 `ghcr.io/biancaveritas/amane` 是两个独立包, 各自的 `latest` 互不影响.
