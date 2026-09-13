# Docker 部署

## 1. 准备服务器

安装 Docker Engine 和 Docker Compose Plugin，开放 `80/443` 端口。建议至少 2 核、4 GB 内存，并为上传文件和向量库预留持久化磁盘。

## 2. 配置环境变量

在项目根目录执行：

```bash
cp deploy/.env.docker.example .env
```

填写模型配置、管理员密码、Session 密钥和 MySQL 密码。`.env` 不要提交到 Git。

## 3. 启动

```bash
docker compose up -d --build
docker compose ps
docker compose logs -f app
```

服务启动后访问 `http://服务器IP:8000`。健康检查地址为 `/health`。

## 4. HTTPS 反向代理

将 `nginx.conf.example` 复制到 Nginx 配置目录，修改域名和证书路径，并把 `proxy_pass` 指向 `127.0.0.1:8000`。如果使用 Docker 暴露端口，也可以把应用端口限制为内网后再由 Nginx 代理。

## 5. 备份与升级

```bash
docker compose exec db mysqldump -u root -p "$MYSQL_DATABASE" > backup.sql
docker compose pull
docker compose up -d --build
```

MySQL 数据保存在 `mysql_data`，上传文件和 Chroma 数据分别保存在 `app_uploads`、`app_chroma`。升级前先备份这三个卷。
