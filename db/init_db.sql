-- 创建数据库
CREATE DATABASE IF NOT EXISTS aidataanalysis
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

-- 创建专用用户（仅本地访问）
CREATE USER IF NOT EXISTS 'aidata'@'localhost' IDENTIFIED BY 'aidata@2026';

-- 授权该用户对 aidataanalysis 库的全部权限
GRANT ALL PRIVILEGES ON aidataanalysis.* TO 'aidata'@'localhost';

FLUSH PRIVILEGES;
