# 署名与分发说明

本项目是 [Amane](https://github.com/sqzw-x/amane) 的衍生分发. 上游版权归 `sqzw-x/amane` 的作者所有, 依 [GNU General Public License v3.0](LICENSE) 授权.

## 代码来源

`hakureireimu123/v0.26.0` 分支的代码取自第三方提供的压缩包 `amane-main-v0.26.0-path-picker-breadcrumb-back.zip`. 该压缩包未附带作者姓名、来源仓库地址与版权声明, 因此本文件暂不标注具体作者.

该版本以官方 v0.26.0 为基础, 由第三方修改并构建, 包含第三方自有功能与站点来源.

本分支由 `BiancaVeritas/amane` 的维护者收录发布, 代码内容保持压缩包原样, 仅做下列改动:

- 新增本文件
- 新增镜像构建工作流 `.github/workflows/docker-hakureireimu123.yml`
- `compose.yaml` 的镜像地址指向 `ghcr.io/biancaveritas/hakureireimu123`
- `README.md` 增加分发说明

## 署名补充

第三方作者可经由 `BiancaVeritas/amane` 的 Issue 提出署名要求与内容更正, 维护者据此补充作者姓名与来源链接.

## 镜像

`ghcr.io/biancaveritas/hakureireimu123` 由本仓库的 GitHub Actions 构建, 与 `custom/*` 分支构建的 `ghcr.io/biancaveritas/amane` 相互独立.
