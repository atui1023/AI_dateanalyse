-- 创建数据库
CREATE DATABASE IF NOT EXISTS aidataanalysis
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

-- 创建专用用户（仅本地访问）
-- 重要：请把 <YOUR_PASSWORD> 替换为你自己的强密码，切勿直接执行此脚本到生产环境
-- 执行后请同步把密码写入项目根目录的 .env 文件（DATABASE_URL 也要对应修改）
CREATE USER IF NOT EXISTS 'aidata'@'localhost' IDENTIFIED BY '<YOUR_PASSWORD>';

-- 授权该用户对 aidataanalysis 库的全部权限
GRANT ALL PRIVILEGES ON aidataanalysis.* TO 'aidata'@'localhost';

FLUSH PRIVILEGES;
