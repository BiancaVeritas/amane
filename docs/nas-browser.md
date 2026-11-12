# NAS 独立 Chromium 浏览器

Aname 的 `remote` 浏览器后端通过 Chromium DevTools Protocol (CDP) 连接独立浏览器容器。Aname 容器与浏览器容器可以分开部署；不需要 PC 常驻，也不需要 BrowserSkill 扩展。

## 1. 部署浏览器容器

以下示例使用 Chromium headless。将 `192.168.1.20` 替换为 NAS 的局域网地址，并将防火墙规则限制为仅允许 Aname 所在主机访问 9222 端口。

```yaml
services:
  amane-chromium:
    image: zenika/alpine-chrome:latest
    container_name: amane-chromium
    command:
      - --headless
      - --no-sandbox
      - --disable-gpu
      - --remote-debugging-address=0.0.0.0
      - --remote-debugging-port=9222
      - --user-data-dir=/data/chromium
      - about:blank
    shm_size: 1gb
    volumes:
      - ./chromium-data:/data/chromium
    ports:
      # 建议绑定 NAS 的 LAN 地址, 不要绑定 0.0.0.0 或映射到公网
      - "192.168.1.20:9222:9222"
    restart: unless-stopped
```

> CDP 端口本身没有身份认证能力。不要将 9222 暴露到互联网；使用局域网绑定、防火墙白名单或容器私有网络限制访问。`--no-sandbox` 是部分容器镜像的运行要求，部署时应使用隔离容器、非特权用户及受限网络。

## 2. Aname 设置

1. 在浏览器渲染设置中将「浏览器后端」选择为 **NAS 独立浏览器**。
2. 「NAS 独立浏览器」地址填写 `http://192.168.1.20:9222`（使用实际 NAS 地址）。
3. 保存设置。
4. 在网络检测页点击「测试联通性」。只有 `/json/version` 返回有效的 `webSocketDebuggerUrl` 才会显示可用；单纯端口开放不会被判定为成功。

如果 Aname 与浏览器容器位于同一个 Docker 网络，也可以填写容器服务名，例如 `http://amane-chromium:9222`，此时不需要发布宿主机端口。

## 3. 会话与验证

Aname 通过 CDP 控制 NAS 容器中的 Chromium，并按来源隔离浏览器 context、复用来源会话。关闭 Aname 后端连接只断开客户端，不主动关闭远端 Chromium 容器。容器重启会清除内存中的页面会话；需要持久化浏览器数据时应保留示例中的数据卷。

NAS 模式使用独立 Chromium 的网络出口，不会自动继承 Aname 的 HTTP 代理设置。请确保 NAS 容器能够访问目标网站。Cloudflare 验证是否能自动通过取决于目标站点和容器出口环境；本模式不保证绕过验证。
