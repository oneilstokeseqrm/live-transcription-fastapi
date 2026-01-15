"""Database connection management for async Postgres operations.

This module provides async connection management using SQLAlchemy's async engine
with SQLModel, configured for serverless environments (Neon).
"""
import os
import ssl
import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, AsyncEngine
from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)

# Global engine instance (initialized lazily)
_engine: AsyncEngine | None = None
_async_session_maker: sessionmaker | None = None


def get_database_url() -> tuple[str, dict]:
    """Get and validate DATABASE_URL from environment.
    
    Returns:
        Tuple of (database URL with asyncpg driver, connect_args dict).
        
    Raises:
        ValueError: If DATABASE_URL is not set.
    """
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise ValueError("DATABASE_URL environment variable is not set")
    
    # Parse the URL to extract and handle query parameters
    parsed = urlparse(database_url)
    query_params = parse_qs(parsed.query)
    
    # Determine SSL settings from query params
    connect_args = {}
    ssl_required = False
    
    if 'sslmode' in query_params:
        sslmode = query_params['sslmode'][0]
        if sslmode in ('require', 'verify-ca', 'verify-full'):
            ssl_required = True
    
    # Remove asyncpg-incompatible parameters from query string
    incompatible_params = ['sslmode', 'channel_binding', 'options']
    filtered_params = {k: v for k, v in query_params.items() if k not in incompatible_params}
    
    # Rebuild URL without incompatible params
    new_query = urlencode(filtered_params, doseq=True) if filtered_params else ''
    
    # Ensure we're using asyncpg driver
    scheme = parsed.scheme
    if scheme == 'postgresql':
        scheme = 'postgresql+asyncpg'
    elif scheme != 'postgresql+asyncpg':
        scheme = 'postgresql+asyncpg'
    
    # Rebuild the URL
    clean_url = urlunparse((
        scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        new_query,
        parsed.fragment
    ))
    
    # Configure SSL for asyncpg if required
    if ssl_required:
        # Create SSL context for asyncpg
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = False
        ssl_context.verify_mode = ssl.CERT_NONE  # Neon uses self-signed certs
        connect_args['ssl'] = ssl_context
    
    return clean_url, connect_args


def get_engine() -> AsyncEngine:
    """Get or create the async database engine.
    
    Uses connection pool settings appropriate for serverless environments.
    
    Returns:
        The AsyncEngine instance.
    """
    global _engine
    
    if _engine is None:
        database_url, connect_args = get_database_url()
        
        _engine = create_async_engine(
            database_url,
            pool_pre_ping=True,  # Verify connections before use
            pool_size=5,  # Base pool size
            max_overflow=10,  # Allow up to 15 total connections
            pool_recycle=300,  # Recycle connections every 5 minutes
            echo=False,  # Set to True for SQL debugging
            connect_args=connect_args,  # SSL and other connection args
        )
        
        logger.info("Database engine created successfully")
    
    return _engine
    
    return _engine


def get_session_maker() -> sessionmaker:
    """Get or create the async session maker.
    
    Returns:
        The sessionmaker configured for async sessions.
    """
    global _async_session_maker
    
    if _async_session_maker is None:
        engine = get_engine()
        _async_session_maker = sessionmaker(
            engine,
            class_=AsyncSession,
            expire_on_commit=False
        )
    
    return _async_session_maker


@asynccontextmanager
async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager for database sessions.
    
    Usage:
        async with get_async_session() as session:
            result = await session.execute(query)
            
    Yields:
        An AsyncSession instance.
    """
    session_maker = get_session_maker()
    async with session_maker() as session:
        try:
            yield session
        except Exception as e:
            await session.rollback()
            logger.error(f"Database session error: {e}", exc_info=True)
            raise
        finally:
            await session.close()


async def close_engine() -> None:
    """Close the database engine and cleanup connections.
    
    Should be called during application shutdown.
    """
    global _engine, _async_session_maker
    
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _async_session_maker = None
        logger.info("Database engine closed")
