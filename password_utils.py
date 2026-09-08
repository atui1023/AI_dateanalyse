"""密码哈希工具：bcrypt 封装。

独立成模块（不放进 auth.py 或 db.py）的原因：
- db.py 的 ensure_default_user 需要哈希密码
- auth.py 的登录/注册也需要哈希/校验
- 若放在 auth.py，db.py 反向 import auth.py 会形成循环依赖
- 本模块只依赖 bcrypt，无其他项目内引用，是依赖链的最底层
"""
import bcrypt

# bcrypt rounds：12 是 2026 年推荐的强度（约 250ms / 次哈希）
ROUNDS = 12


def hash_password(plain: str) -> str:
    """把明文密码哈希成 bcrypt 字符串（含 salt 和版本前缀，可直接存数据库）"""
    if not plain:
        raise ValueError("密码不能为空")
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt(rounds=ROUNDS)).decode("utf-8")


def verify_password(plain: str, hashed: str) -> bool:
    """校验明文密码是否匹配 bcrypt 哈希"""
    if not plain or not hashed:
        return False
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except (ValueError, TypeError):
        # 哈希格式不合法（例如旧的 "!" 占位）
        return False
