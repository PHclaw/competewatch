"""
CompeteWatch Backend - 竞品监控SaaS
"""
import os
import json
import hashlib
from datetime import datetime
from typing import Optional, List
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, HttpUrl
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, Float, JSON, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from apscheduler.schedulers.background import BackgroundScheduler

# Database setup
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./competewatch.db")
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# Models
class Competitor(Base):
    """竞品"""
    __tablename__ = "competitors"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    url = Column(String(500), nullable=False)
    monitor_type = Column(String(50), default="price")  # price, changelog, article, seo
    selector = Column(String(200))  # CSS选择器
    check_interval = Column(Integer, default=3600)  # 检查间隔（秒）
    last_check = Column(DateTime)
    last_value = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    changes = relationship("Change", back_populates="competitor")

class Change(Base):
    """变化记录"""
    __tablename__ = "changes"
    
    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id"))
    change_type = Column(String(50))  # price_change, new_article, feature_update
    old_value = Column(Text)
    new_value = Column(Text)
    diff = Column(Text)
    detected_at = Column(DateTime, default=datetime.utcnow)
    notified = Column(Boolean, default=False)
    
    competitor = relationship("Competitor", back_populates="changes")

Base.metadata.create_all(bind=engine)

# Pydantic schemas
class CompetitorCreate(BaseModel):
    name: str
    url: str
    monitor_type: str = "price"
    selector: Optional[str] = None
    check_interval: int = 3600

class CompetitorResponse(BaseModel):
    id: int
    name: str
    url: str
    monitor_type: str
    selector: Optional[str]
    check_interval: int
    last_check: Optional[datetime]
    last_value: Optional[str]
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

class ChangeResponse(BaseModel):
    id: int
    competitor_id: int
    change_type: str
    old_value: Optional[str]
    new_value: Optional[str]
    detected_at: datetime
    notified: bool
    
    class Config:
        from_attributes = True

# Dependency
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# App
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: init scheduler
    scheduler = BackgroundScheduler()
    scheduler.add_job(check_all_competitors, 'interval', minutes=30, id='check_competitors')
    scheduler.start()
    yield
    # Shutdown
    scheduler.shutdown()

app = FastAPI(title="CompeteWatch", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Monitor functions
def fetch_page(url: str) -> str:
    """获取页面内容"""
    import requests
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text

def extract_value(html: str, selector: str, monitor_type: str) -> str:
    """提取监控值"""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, 'lxml')
    
    if selector:
        elem = soup.select_one(selector)
        return elem.get_text(strip=True) if elem else ""
    
    # 默认提取策略
    if monitor_type == "price":
        # 尝试常见的价格选择器
        for sel in [".price", ".amount", "[data-price]", ".product-price"]:
            elem = soup.select_one(sel)
            if elem:
                return elem.get_text(strip=True)
    
    return soup.get_text(strip=True)[:500]

def check_competitor(competitor_id: int):
    """检查单个竞品"""
    db = SessionLocal()
    try:
        comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
        if not comp or not comp.is_active:
            return
        
        try:
            html = fetch_page(comp.url)
            new_value = extract_value(html, comp.selector, comp.monitor_type)
            new_hash = hashlib.md5(new_value.encode()).hexdigest()
            
            # 检测变化
            if comp.last_value:
                old_hash = hashlib.md5(comp.last_value.encode()).hexdigest()
                if old_hash != new_hash:
                    # 记录变化
                    change = Change(
                        competitor_id=comp.id,
                        change_type=f"{comp.monitor_type}_change",
                        old_value=comp.last_value,
                        new_value=new_value
                    )
                    db.add(change)
                    
                    # TODO: 发送通知
            
            comp.last_value = new_value
            comp.last_check = datetime.utcnow()
            db.commit()
            
        except Exception as e:
            print(f"检查失败 {comp.name}: {e}")
            
    finally:
        db.close()

def check_all_competitors():
    """检查所有活跃竞品"""
    db = SessionLocal()
    try:
        competitors = db.query(Competitor).filter(Competitor.is_active == True).all()
        for comp in competitors:
            if not comp.last_check or \
               (datetime.utcnow() - comp.last_check).total_seconds() >= comp.check_interval:
                check_competitor(comp.id)
    finally:
        db.close()

# API routes
@app.get("/", response_class=HTMLResponse)
async def root():
    import os
    frontend_path = os.path.join(os.path.dirname(__file__), "..", "frontend", "index.html")
    if os.path.exists(frontend_path):
        with open(frontend_path, "r", encoding="utf-8") as f:
            return f.read()
    return "<h1>CompeteWatch</h1><p>Frontend not found. API docs at <a href='/docs'>/docs</a></p>"

@app.get("/api")
async def api_root():
    return {"message": "CompeteWatch API", "docs": "/docs"}

@app.get("/api/competitors", response_model=List[CompetitorResponse])
async def list_competitors(db: Session = Depends(get_db)):
    return db.query(Competitor).all()

@app.post("/api/competitors", response_model=CompetitorResponse)
async def create_competitor(data: CompetitorCreate, db: Session = Depends(get_db)):
    comp = Competitor(**data.dict())
    db.add(comp)
    db.commit()
    db.refresh(comp)
    return comp

@app.get("/api/competitors/{id}", response_model=CompetitorResponse)
async def get_competitor(id: int, db: Session = Depends(get_db)):
    comp = db.query(Competitor).filter(Competitor.id == id).first()
    if not comp:
        raise HTTPException(404, "Competitor not found")
    return comp

@app.delete("/api/competitors/{id}")
async def delete_competitor(id: int, db: Session = Depends(get_db)):
    comp = db.query(Competitor).filter(Competitor.id == id).first()
    if not comp:
        raise HTTPException(404, "Competitor not found")
    db.delete(comp)
    db.commit()
    return {"ok": True}

@app.post("/api/competitors/{id}/check")
async def manual_check(id: int, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    comp = db.query(Competitor).filter(Competitor.id == id).first()
    if not comp:
        raise HTTPException(404, "Competitor not found")
    background_tasks.add_task(check_competitor, id)
    return {"message": "Check started"}

@app.get("/api/competitors/{id}/changes", response_model=List[ChangeResponse])
async def get_changes(id: int, limit: int = 50, db: Session = Depends(get_db)):
    return db.query(Change).filter(Change.competitor_id == id).order_by(Change.detected_at.desc()).limit(limit).all()

@app.get("/api/dashboard")
async def dashboard(db: Session = Depends(get_db)):
    total = db.query(Competitor).count()
    active = db.query(Competitor).filter(Competitor.is_active == True).count()
    recent_changes = db.query(Change).order_by(Change.detected_at.desc()).limit(10).all()
    
    return {
        "total_competitors": total,
        "active_competitors": active,
        "recent_changes": len(recent_changes),
        "changes": [
            {
                "id": c.id,
                "competitor_id": c.competitor_id,
                "competitor_name": db.query(Competitor).get(c.competitor_id).name if c.competitor_id else "Unknown",
                "change_type": c.change_type,
                "detected_at": c.detected_at.isoformat()
            }
            for c in recent_changes
        ]
    }

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8080)
