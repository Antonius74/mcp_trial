from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, EmailStr, Field


class CustomerCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr


class CustomerOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    created_at: datetime


class OrderCreate(BaseModel):
    customer_id: int = Field(gt=0)
    item: str = Field(min_length=1, max_length=120)
    amount: Decimal = Field(ge=0)
    status: str = Field(default="new", min_length=1, max_length=40)


class OrderOut(BaseModel):
    id: int
    customer_id: int
    item: str
    amount: Decimal
    status: str
    created_at: datetime
