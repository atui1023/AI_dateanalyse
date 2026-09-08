-- ============================================================
-- 智能数据分析助手 - 数据库表结构
-- 数据库: aidataanalysis
-- 字符集: utf8mb4
-- ============================================================

USE aidataanalysis;

-- ---------- 1. 用户表 ----------
CREATE TABLE IF NOT EXISTS users (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    username        VARCHAR(64)  NOT NULL UNIQUE COMMENT '登录用户名',
    password_hash   VARCHAR(255) NOT NULL COMMENT 'bcrypt 哈希密码',
    display_name    VARCHAR(64)  DEFAULT NULL COMMENT '显示名称',
    role            VARCHAR(16)  NOT NULL DEFAULT 'user' COMMENT '角色: user/admin',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_username (username)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='用户表';

-- ---------- 2. 知识库文件夹表 ----------
CREATE TABLE IF NOT EXISTS kb_folders (
    id              VARCHAR(32)  PRIMARY KEY COMMENT '文件夹ID(UUID)',
    user_id         BIGINT       NOT NULL COMMENT '所属用户',
    name            VARCHAR(128) NOT NULL COMMENT '文件夹名称',
    is_system       TINYINT(1)   NOT NULL DEFAULT 0 COMMENT '是否系统文件夹(0否1是)',
    is_active       TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '是否参与检索',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    CONSTRAINT fk_folder_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库文件夹';

-- ---------- 3. 知识库文档表 ----------
CREATE TABLE IF NOT EXISTS kb_documents (
    doc_id          VARCHAR(32)  PRIMARY KEY COMMENT '文档ID(UUID)',
    user_id         BIGINT       NOT NULL COMMENT '所属用户',
    folder_id       VARCHAR(32)  DEFAULT NULL COMMENT '所属文件夹',
    filename        VARCHAR(255) NOT NULL COMMENT '原始文件名',
    file_path       VARCHAR(512) NOT NULL COMMENT '服务器存储路径',
    file_ext        VARCHAR(16)  DEFAULT NULL COMMENT '文件扩展名',
    file_size       BIGINT       DEFAULT 0 COMMENT '文件大小(字节)',
    status          VARCHAR(16)  NOT NULL DEFAULT 'parsing' COMMENT '状态: parsing/ready/failed',
    chunks          INT          DEFAULT 0 COMMENT '切分块数',
    error_msg       TEXT         DEFAULT NULL COMMENT '解析失败原因',
    is_active       TINYINT(1)   NOT NULL DEFAULT 1 COMMENT '是否参与检索',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user_folder (user_id, folder_id),
    INDEX idx_status (status),
    CONSTRAINT fk_doc_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    CONSTRAINT fk_doc_folder FOREIGN KEY (folder_id) REFERENCES kb_folders(id) ON DELETE SET NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='知识库文档';

-- ---------- 4. 数据集(分析用表格)表 ----------
CREATE TABLE IF NOT EXISTS datasets (
    dataset_id      VARCHAR(32)  PRIMARY KEY COMMENT '数据集ID(UUID)',
    user_id         BIGINT       NOT NULL COMMENT '所属用户',
    filename        VARCHAR(255) NOT NULL COMMENT '原始文件名',
    file_path       VARCHAR(512) NOT NULL COMMENT '服务器存储路径',
    file_ext        VARCHAR(16)  DEFAULT NULL COMMENT '文件扩展名',
    row_count       INT          DEFAULT 0 COMMENT '行数',
    col_count       INT          DEFAULT 0 COMMENT '列数',
    columns_info    JSON         DEFAULT NULL COMMENT '列名与类型信息',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    CONSTRAINT fk_dataset_user FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='分析数据集';

-- ---------- 5. 会话表 ----------
CREATE TABLE IF NOT EXISTS sessions (
    session_id      VARCHAR(32)  PRIMARY KEY COMMENT '会话ID(UUID)',
    user_id         BIGINT       NOT NULL COMMENT '所属用户',
    title           VARCHAR(255) DEFAULT NULL COMMENT '会话标题',
    mode            VARCHAR(16)  DEFAULT 'chat' COMMENT '模式: chat/analysis/rag',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='对话会话';

-- ---------- 6. 对话消息表 ----------
CREATE TABLE IF NOT EXISTS chat_messages (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    session_id      VARCHAR(32)  NOT NULL COMMENT '所属会话',
    user_id         BIGINT       NOT NULL COMMENT '所属用户',
    role            VARCHAR(16)  NOT NULL COMMENT '角色: user/assistant/system',
    content         MEDIUMTEXT   NOT NULL COMMENT '消息内容',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_session (session_id),
    INDEX idx_user (user_id),
    CONSTRAINT fk_msg_session FOREIGN KEY (session_id) REFERENCES sessions(session_id) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='对话消息';

-- ---------- 7. 分析结果记录表 ----------
CREATE TABLE IF NOT EXISTS analysis_results (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id         BIGINT       NOT NULL COMMENT '所属用户',
    session_id      VARCHAR(32)  DEFAULT NULL COMMENT '所属会话',
    question        TEXT         NOT NULL COMMENT '用户问题',
    code            MEDIUMTEXT   DEFAULT NULL COMMENT '生成的分析代码',
    stdout          MEDIUMTEXT   DEFAULT NULL COMMENT '执行标准输出',
    table_json      MEDIUMTEXT   DEFAULT NULL COMMENT '结果表格(JSON)',
    chart_json      MEDIUMTEXT   DEFAULT NULL COMMENT '图表配置(JSON)',
    error_msg       TEXT         DEFAULT NULL COMMENT '执行错误信息',
    status          VARCHAR(16)  NOT NULL DEFAULT 'running' COMMENT '状态: running/success/failed',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_session (session_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='分析结果记录';

-- ---------- 8. 操作审计日志表 ----------
CREATE TABLE IF NOT EXISTS audit_logs (
    id              BIGINT AUTO_INCREMENT PRIMARY KEY,
    user_id         BIGINT       DEFAULT NULL COMMENT '操作用户',
    action          VARCHAR(64)  NOT NULL COMMENT '操作类型',
    target_type     VARCHAR(32)  DEFAULT NULL COMMENT '操作对象类型',
    target_id       VARCHAR(64)  DEFAULT NULL COMMENT '操作对象ID',
    detail          TEXT         DEFAULT NULL COMMENT '操作详情',
    ip_address      VARCHAR(64)  DEFAULT NULL COMMENT 'IP地址',
    created_at      DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_user (user_id),
    INDEX idx_action (action),
    INDEX idx_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci COMMENT='操作审计日志';
