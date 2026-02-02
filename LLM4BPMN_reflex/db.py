"""Database module for storing BPMN generation results"""

import os
import json
import logging
from datetime import datetime
from typing import List, Dict, Any, Optional

import psycopg2
from psycopg2.extras import RealDictCursor
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

logger = logging.getLogger(__name__)

# Database connection URL
DATABASE_URL = os.getenv("DATABASE_URL")


def get_connection():
    """Create a database connection"""
    if not DATABASE_URL:
        raise ValueError("DATABASE_URL not set in environment variables")
    return psycopg2.connect(DATABASE_URL)


def init_database():
    """Initialize the database schema - create tables if they don't exist"""
    create_table_sql = """
    CREATE TABLE IF NOT EXISTS bpmn_generations (
        id SERIAL PRIMARY KEY,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        process_type VARCHAR(255),
        ai_model VARCHAR(100),
        chat_history JSONB,
        interview_summary TEXT,
        bpmn_xml TEXT,
        generation_duration_seconds FLOAT
    );
    
    CREATE INDEX IF NOT EXISTS idx_bpmn_generations_created_at 
    ON bpmn_generations(created_at DESC);
    
    CREATE INDEX IF NOT EXISTS idx_bpmn_generations_process_type 
    ON bpmn_generations(process_type);
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(create_table_sql)
            conn.commit()
        logger.info("Database initialized successfully")
        return True
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
        return False


def save_bpmn_generation(
    process_type: str,
    ai_model: str,
    chat_history: List[Dict[str, Any]],
    interview_summary: str,
    bpmn_xml: str,
    generation_duration_seconds: Optional[float] = None
) -> Optional[int]:
    """
    Save a BPMN generation record to the database
    
    Args:
        process_type: Type of process (e.g., "Angebots- und Auftragserstellung")
        ai_model: AI model used (e.g., "gpt-5.2")
        chat_history: List of chat messages
        interview_summary: Summary of the interview
        bpmn_xml: Generated BPMN XML
        generation_duration_seconds: How long the generation took
        
    Returns:
        The ID of the inserted record, or None if failed
    """
    insert_sql = """
    INSERT INTO bpmn_generations 
    (process_type, ai_model, chat_history, interview_summary, bpmn_xml, generation_duration_seconds)
    VALUES (%s, %s, %s, %s, %s, %s)
    RETURNING id;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(insert_sql, (
                    process_type,
                    ai_model,
                    json.dumps(chat_history, ensure_ascii=False),
                    interview_summary,
                    bpmn_xml,
                    generation_duration_seconds
                ))
                record_id = cur.fetchone()[0]
            conn.commit()
        logger.info(f"BPMN generation saved with ID: {record_id}")
        return record_id
    except Exception as e:
        logger.error(f"Error saving BPMN generation: {e}")
        return None


def get_all_generations(limit: int = 100) -> List[Dict[str, Any]]:
    """
    Get all BPMN generation records
    
    Args:
        limit: Maximum number of records to return
        
    Returns:
        List of generation records
    """
    select_sql = """
    SELECT id, created_at, process_type, ai_model, chat_history, 
           interview_summary, bpmn_xml, generation_duration_seconds
    FROM bpmn_generations
    ORDER BY created_at DESC
    LIMIT %s;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(select_sql, (limit,))
                records = cur.fetchall()
        return [dict(r) for r in records]
    except Exception as e:
        logger.error(f"Error fetching BPMN generations: {e}")
        return []


def get_generation_by_id(generation_id: int) -> Optional[Dict[str, Any]]:
    """
    Get a specific BPMN generation record by ID
    
    Args:
        generation_id: The ID of the record
        
    Returns:
        The generation record, or None if not found
    """
    select_sql = """
    SELECT id, created_at, process_type, ai_model, chat_history, 
           interview_summary, bpmn_xml, generation_duration_seconds
    FROM bpmn_generations
    WHERE id = %s;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(select_sql, (generation_id,))
                record = cur.fetchone()
        return dict(record) if record else None
    except Exception as e:
        logger.error(f"Error fetching BPMN generation {generation_id}: {e}")
        return None


def get_generation_stats() -> Dict[str, Any]:
    """
    Get statistics about BPMN generations
    
    Returns:
        Dictionary with stats
    """
    stats_sql = """
    SELECT 
        COUNT(*) as total_generations,
        COUNT(DISTINCT process_type) as unique_process_types,
        AVG(generation_duration_seconds) as avg_duration_seconds,
        MIN(created_at) as first_generation,
        MAX(created_at) as last_generation
    FROM bpmn_generations;
    """
    
    process_type_sql = """
    SELECT process_type, COUNT(*) as count
    FROM bpmn_generations
    GROUP BY process_type
    ORDER BY count DESC;
    """
    
    try:
        with get_connection() as conn:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                cur.execute(stats_sql)
                stats = dict(cur.fetchone())
                
                cur.execute(process_type_sql)
                stats['process_type_distribution'] = [dict(r) for r in cur.fetchall()]
                
        return stats
    except Exception as e:
        logger.error(f"Error fetching stats: {e}")
        return {}


# Initialize database on module import
try:
    init_database()
except Exception as e:
    logger.warning(f"Could not initialize database on import: {e}")
