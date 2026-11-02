"""Backend entry point used by the desktop shell."""
import uvicorn
from backend.config import get_settings
if __name__ == "__main__":
    s=get_settings()
    uvicorn.run("backend.main:app",host=s.host,port=s.port,log_level=s.log_level.lower())
