from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """個別テーブルの ORM モデルは各 API の依頼単位で追加する(依頼 0-1 の範囲外)。"""
