"""
CompeteWatch Backend - 竞品监控SaaS
v1.1 - 新增通知系统、价格趋势、监控快照
"""
import os
import json
import hashlib
import smtplib
import requests
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException, BackgroundTasks, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, HttpUrl, EmailStr
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime, Boolean, Float, JSON, ForeignKey, desc
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session, relationship
from apscheduler.schedulers.background import BackgroundScheduler

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    last_price = Column(Float)  # 解析出的价格数值
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    
    changes = relationship("Change", back_populates="competitor")
    snapshots = relationship("Snapshot", back_populates="competitor")

class Change(Base):
    """变化记录"""
    __tablename__ = "changes"
    
    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id"))
    change_type = Column(String(50))  # price_change, new_article, feature_update
    old_value = Column(Text)
    new_value = Column(Text)
    old_price = Column(Float)
    new_price = Column(Float)
    diff = Column(Text)
    detected_at = Column(DateTime, default=datetime.utcnow)
    notified = Column(Boolean, default=False)
    
    competitor = relationship("Competitor", back_populates="changes")

class Snapshot(Base):
    """监控快照"""
    __tablename__ = "snapshots"
    
    id = Column(Integer, primary_key=True, index=True)
    competitor_id = Column(Integer, ForeignKey("competitors.id"))
    value = Column(Text)
    price = Column(Float)
    html_hash = Column(String(32))
    captured_at = Column(DateTime, default=datetime.utcnow)
    
    competitor = relationship("Competitor", back_populates="snapshots")

class NotificationConfig(Base):
    """通知配置"""
    __tablename__ = "notification_configs"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100))
    notify_type = Column(String(20))  # email, webhook
    is_active = Column(Boolean, default=True)
    
    # 邮件配置
    smtp_host = Column(String(100))
    smtp_port = Column(Integer, default=587)
    smtp_user = Column(String(100))
    smtp_pass = Column(String(100))
    email_to = Column(String(500))  # 多个邮箱用逗号分隔
    
    # Webhook配置
    webhook_url = Column(String(500))
    webhook_method = Column(String(10), default="POST")
    webhook_headers = Column(JSON)
    
    created_at = Column(DateTime, default=datetime.utcnow)

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
    last_price: Optional[float]
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
    old_price: Optional[float]
    new_price: Optional[float]
    detected_at: datetime
    notified: bool
    
    class Config:
        from_attributes = True

class NotificationConfigCreate(BaseModel):
    name: str
    notify_type: str  # email, webhook
    smtp_host: Optional[str] = None
    smtp_port: Optional[int] = 587
    smtp_user: Optional[str] = None
    smtp_pass: Optional[str] = None
    email_to: Optional[str] = None
    webhook_url: Optional[str] = None
    webhook_method: Optional[str] = "POST"
    webhook_headers: Optional[Dict[str, str]] = None

class NotificationConfigResponse(BaseModel):
    id: int
    name: str
    notify_type: str
    is_active: bool
    created_at: datetime
    
    class Config:
        from_attributes = True

class TrendPoint(BaseModel):
    date: str
    price: Optional[float]
    value: Optional[str]

class TrendResponse(BaseModel):
    competitor_id: int
    competitor_name: str
    points: List[TrendPoint]

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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"
    }
    resp = requests.get(url, headers=headers, timeout=30)
    resp.raise_for_status()
    return resp.text

def extract_value(html: str, selector: str, monitor_type: str) -> tuple[str, Optional[float]]:
    """提取监控值，返回 (文本值, 价格数值)"""
    from bs4 import BeautifulSoup
    import re
    
    soup = BeautifulSoup(html, 'lxml')
    price = None
    
    if selector:
        elem = soup.select_one(selector)
        text = elem.get_text(strip=True) if elem else ""
    else:
        # 默认提取策略
        if monitor_type == "price":
            for sel in [".price", ".amount", "[data-price]", ".product-price", ".current-price"]:
                elem = soup.select_one(sel)
                if elem:
                    text = elem.get_text(strip=True)
                    break
            else:
                text = ""
        else:
            text = soup.get_text(strip=True)[:500]
    
    # 尝试提取价格数值
    if monitor_type == "price":
        # 匹配 ¥99.99 $99.99 99.99元 等
        price_match = re.search(r'[¥$￥]?\s*(\d+(?:,\d{3})*(?:\.\d{1,2})?)', text)
        if price_match:
            price = float(price_match.group(1).replace(',', ''))
    
    return text, price

def send_email_notification(config: NotificationConfig, subject: str, body: str):
    """发送邮件通知"""
    try:
        msg = MIMEMultipart()
        msg['From'] = config.smtp_user
        msg['To'] = config.email_to
        msg['Subject'] = subject
        
        msg.attach(MIMEText(body, 'html', 'utf-8'))
        
        with smtplib.SMTP(config.smtp_host, config.smtp_port) as server:
            server.starttls()
            server.login(config.smtp_user, config.smtp_pass)
            server.send_message(msg)
        
        logger.info(f"邮件发送成功: {config.email_to}")
        return True
    except Exception as e:
        logger.error(f"邮件发送失败: {e}")
        return False

def send_webhook_notification(config: NotificationConfig, data: Dict[str, Any]):
    """发送Webhook通知"""
    try:
        headers = config.webhook_headers or {}
        headers['Content-Type'] = 'application/json'
        
        resp = requests.request(
            method=config.webhook_method,
            url=config.webhook_url,
            json=data,
            headers=headers,
            timeout=10
        )
        resp.raise_for_status()
        
        logger.info(f"Webhook发送成功: {config.webhook_url}")
        return True
    except Exception as e:
        logger.error(f"Webhook发送失败: {e}")
        return False

def send_notifications(competitor: Competitor, change: Change):
    """发送所有激活的通知"""
    db = SessionLocal()
    try:
        configs = db.query(NotificationConfig).filter(NotificationConfig.is_active == True).all()
        
        for config in configs:
            subject = f"[CompeteWatch] {competitor.name} 发生变化"
            
            if config.notify_type == "email":
                body = f"""
                <h2>竞品变化通知</h2>
                <p><b>竞品:</b> {competitor.name}</p>
                <p><b>URL:</b> <a href="{competitor.url}">{competitor.url}</a></p>
                <p><b>变化类型:</b> {change.change_type}</p>
                <p><b>旧值:</b> {change.old_value}</p>
                <p><b>新值:</b> {change.new_value}</p>
                <p><b>检测时间:</b> {change.detected_at.strftime('%Y-%m-%d %H:%M:%S')}</p>
                <hr>
                <p><a href="http://localhost:8080">查看详情</a></p>
                """
                send_email_notification(config, subject, body)
            
            elif config.notify_type == "webhook":
                data = {
                    "competitor_id": competitor.id,
                    "competitor_name": competitor.name,
                    "competitor_url": competitor.url,
                    "change_type": change.change_type,
                    "old_value": change.old_value,
                    "new_value": change.new_value,
                    "old_price": change.old_price,
                    "new_price": change.new_price,
                    "detected_at": change.detected_at.isoformat()
                }
                send_webhook_notification(config, data)
        
        # 标记已通知
        change.notified = True
        db.commit()
    finally:
        db.close()

def check_competitor(competitor_id: int):
    """检查单个竞品"""
    db = SessionLocal()
    try:
        comp = db.query(Competitor).filter(Competitor.id == competitor_id).first()
        if not comp or not comp.is_active:
            return
        
        try:
            html = fetch_page(comp.url)
            new_value, new_price = extract_value(html, comp.selector, comp.monitor_type)
            new_hash = hashlib.md5(new_value.encode()).hexdigest()
            
            # 保存快照
            snapshot = Snapshot(
                competitor_id=comp.id,
                value=new_value,
                price=new_price,
                html_hash=new_hash
            )
            db.add(snapshot)
            
            # 检测变化
            if comp.last_value:
                old_hash = hashlib.md5(comp.last_value.encode()).hexdigest()
                if old_hash != new_hash:
                    # 记录变化
                    change = Change(
                        competitor_id=comp.id,
                        change_type=f"{comp.monitor_type}_change",
                        old_value=comp.last_value,
                        new_value=new_value,
                        old_price=comp.last_price,
                        new_price=new_price
                    )
                    db.add(change)
                    db.commit()
                    
                    # 发送通知
                    send_notifications(comp, change)
                    
                    logger.info(f"检测到变化: {comp.name}")
            
            comp.last_value = new_value
            comp.last_price = new_price
            comp.last_check = datetime.utcnow()
            db.commit()
            
        except Exception as e:
            logger.error(f"检查失败 {comp.name}: {e}")
            
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

@app.get("/api/competitors/{id}/snapshots")
async def get_snapshots(id: int, limit: int = 100, db: Session = Depends(get_db)):
    """获取快照列表"""
    snapshots = db.query(Snapshot).filter(Snapshot.competitor_id == id).order_by(Snapshot.captured_at.desc()).limit(limit).all()
    return [{"id": s.id, "value": s.value, "price": s.price, "captured_at": s.captured_at.isoformat()} for s in snapshots]

@app.get("/api/competitors/{id}/trend", response_model=TrendResponse)
async def get_trend(id: int, days: int = 30, db: Session = Depends(get_db)):
    """获取价格趋势"""
    comp = db.query(Competitor).filter(Competitor.id == id).first()
    if not comp:
        raise HTTPException(404, "Competitor not found")
    
    start_date = datetime.utcnow() - timedelta(days=days)
    snapshots = db.query(Snapshot).filter(
        Snapshot.competitor_id == id,
        Snapshot.captured_at >= start_date
    ).order_by(Snapshot.captured_at).all()
    
    points = []
    for s in snapshots:
        points.append({
            "date": s.captured_at.strftime("%Y-%m-%d %H:%M"),
            "price": s.price,
            "value": s.value[:100] if s.value else None
        })
    
    return {
        "competitor_id": id,
        "competitor_name": comp.name,
        "points": points
    }

@app.patch("/api/competitors/{id}/toggle")
async def toggle_competitor(id: int, db: Session = Depends(get_db)):
    """切换竞品激活状态"""
    comp = db.query(Competitor).filter(Competitor.id == id).first()
    if not comp:
        raise HTTPException(404, "Competitor not found")
    comp.is_active = not comp.is_active
    db.commit()
    return {"id": comp.id, "is_active": comp.is_active}

# Notification Config APIs
@app.get("/api/notifications", response_model=List[NotificationConfigResponse])
async def list_notifications(db: Session = Depends(get_db)):
    return db.query(NotificationConfig).all()

@app.post("/api/notifications", response_model=NotificationConfigResponse)
async def create_notification(data: NotificationConfigCreate, db: Session = Depends(get_db)):
    config = NotificationConfig(**data.dict())
    db.add(config)
    db.commit()
    db.refresh(config)
    return config

@app.patch("/api/notifications/{id}/toggle")
async def toggle_notification(id: int, db: Session = Depends(get_db)):
    """切换通知激活状态"""
    config = db.query(NotificationConfig).filter(NotificationConfig.id == id).first()
    if not config:
        raise HTTPException(404, "Notification config not found")
    config.is_active = not config.is_active
    db.commit()
    return {"id": config.id, "is_active": config.is_active}

@app.delete("/api/notifications/{id}")
async def delete_notification(id: int, db: Session = Depends(get_db)):
    config = db.query(NotificationConfig).filter(NotificationConfig.id == id).first()
    if not config:
        raise HTTPException(404, "Notification config not found")
    db.delete(config)
    db.commit()
    return {"ok": True}

@app.post("/api/notifications/test")
async def test_notification(id: int, db: Session = Depends(get_db)):
    """测试通知配置"""
    config = db.query(NotificationConfig).filter(NotificationConfig.id == id).first()
    if not config:
        raise HTTPException(404, "Notification config not found")
    
    if config.notify_type == "email":
        success = send_email_notification(
            config,
            "[CompeteWatch] 测试通知",
            "<h1>这是一条测试通知</h1><p>如果您收到此邮件，说明通知配置正常。</p>"
        )
    elif config.notify_type == "webhook":
        success = send_webhook_notification(config, {
            "test": True,
            "message": "CompeteWatch 测试通知",
            "timestamp": datetime.utcnow().isoformat()
        })
    else:
        raise HTTPException(400, "Unknown notification type")
    
    return {"success": success}

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
