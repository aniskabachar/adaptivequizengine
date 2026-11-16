from datetime import timedelta
from typing import Optional
from jose import jwt
import bcrypt
from app.core.config import settings
from app.core.time_utils import utcnow



def _pw_bytes(password: str) -> bytes:
    # bcrypt only uses the first 72 bytes; bcrypt>=4.1 raises instead of truncating
    return password.encode("utf-8")[:72]

class SecurityUtils:
    """
    Security related utilities.
    """
    
    def hash_password(self, password: str) -> str:
        return bcrypt.hashpw(_pw_bytes(password), bcrypt.gensalt()).decode("utf-8")
        
    def verify_password(self, plain_password: str, hashed_password: str) -> bool:
        try:
            return bcrypt.checkpw(_pw_bytes(plain_password), hashed_password.encode("utf-8"))
        except (ValueError, TypeError):
            return False
        
    def create_access_token(self, data: dict, expires_delta: Optional[timedelta] = None) -> str:
        to_encode = data.copy()
        if expires_delta:
            expire = utcnow() + expires_delta
        else:
            expire = utcnow() + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
        
        to_encode.update({"exp": expire})
        encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
        return encoded_jwt

security_utils = SecurityUtils()

