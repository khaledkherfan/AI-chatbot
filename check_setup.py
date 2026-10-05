"""
Setup checker - validates that all components are properly configured.
Run this to diagnose issues with the system.

Usage: python check_setup.py
"""
import os
import sys

def check_env():
    """Check environment variables."""
    print("\n📋 Environment Variables:")
    print("-" * 40)
    
    issues = []
    
    # Check .env file
    if os.path.exists('.env'):
        print("   ✓ .env file exists")
    else:
        print("   ⚠️ .env file not found")
        issues.append("Create .env file with OPENAI_API_KEY")
    
    # Check OpenAI key
    from dotenv import load_dotenv
    load_dotenv()
    
    api_key = os.getenv('OPENAI_API_KEY', '')
    if api_key and len(api_key) > 20:
        print(f"   ✓ OPENAI_API_KEY configured ({api_key[:8]}...)")
    else:
        print("   ❌ OPENAI_API_KEY not set or invalid")
        issues.append("Set OPENAI_API_KEY in .env")
    
    return issues


def check_redis():
    """Check Redis connection."""
    print("\n🗄️ Redis Connection:")
    print("-" * 40)
    
    issues = []
    
    try:
        from services.redis_service import RedisService
        redis = RedisService()
        
        if redis.is_connected():
            print("   ✓ Redis connected")
            
            # Get stats
            stats = redis.get_stats()
            print(f"   ✓ Data keys: {stats.get('total_data_keys', 0)}")
            print(f"   ✓ Aliases: {stats.get('total_aliases', 0)}")
            print(f"   ✓ Embeddings: {stats.get('total_embeddings', 0)}")
            
            if stats.get('total_aliases', 0) == 0:
                issues.append("No aliases in Redis - run: python seed_data.py")
        else:
            print("   ❌ Redis not connected")
            issues.append("Start Redis server")
    except Exception as e:
        print(f"   ❌ Redis error: {e}")
        issues.append("Fix Redis connection")
    
    return issues


def check_openai():
    """Check OpenAI API."""
    print("\n🤖 OpenAI API:")
    print("-" * 40)
    
    issues = []
    
    try:
        from services.openai_service import OpenAIService
        openai = OpenAIService()
        
        if openai.is_configured():
            print("   ✓ OpenAI client configured")
            print(f"   ✓ Model: {openai.model}")
            
            # Test API call
            try:
                from openai import OpenAI
                from config import openai_client_kwargs

                kw = openai_client_kwargs()
                client = OpenAI(**kw) if kw else None
                if not client:
                    raise RuntimeError("OPENAI_API_KEY not set")
                response = client.models.list()
                print("   ✓ API connection successful")
            except Exception as e:
                error_msg = str(e)
                if "invalid_api_key" in error_msg:
                    print("   ❌ Invalid API key")
                    issues.append("Check OPENAI_API_KEY - it may be expired or invalid")
                else:
                    print(f"   ⚠️ API test failed: {error_msg[:50]}")
        else:
            print("   ❌ OpenAI not configured")
            issues.append("Set OPENAI_API_KEY")
    except Exception as e:
        print(f"   ❌ OpenAI error: {e}")
        issues.append("Fix OpenAI configuration")
    
    return issues


def check_embeddings():
    """Check embeddings service."""
    print("\n🧮 Embeddings Service:")
    print("-" * 40)
    
    issues = []
    
    try:
        from services.embeddings_service import EmbeddingsService
        embeddings = EmbeddingsService()
        
        if embeddings.is_configured():
            print("   ✓ Embeddings service configured")
            print(f"   ✓ Model: {embeddings.model}")
            print(f"   ✓ Threshold: {embeddings.threshold}")
            
            # Test embedding generation
            try:
                emb = embeddings.generate_embedding("test")
                if emb:
                    print(f"   ✓ Test embedding generated (dim: {len(emb)})")
                else:
                    print("   ⚠️ Test embedding failed")
                    issues.append("Embeddings generation may have issues")
            except Exception as e:
                print(f"   ⚠️ Embedding test failed: {e}")
        else:
            print("   ❌ Embeddings not configured (need OPENAI_API_KEY)")
            issues.append("Configure OpenAI for embeddings")
    except Exception as e:
        print(f"   ❌ Embeddings error: {e}")
    
    return issues


def check_resources():
    """Check resources.json."""
    print("\n📁 Resources File:")
    print("-" * 40)
    
    issues = []
    
    try:
        import json
        with open('resources.json', 'r') as f:
            resources = json.load(f)
        
        print(f"   ✓ resources.json loaded")
        print(f"   ✓ {len(resources)} resource URLs")
        
        for key in list(resources.keys())[:5]:
            print(f"      - {key}")
        if len(resources) > 5:
            print(f"      - ... and {len(resources) - 5} more")
    except FileNotFoundError:
        print("   ❌ resources.json not found")
        issues.append("Create resources.json")
    except Exception as e:
        print(f"   ❌ Error: {e}")
    
    return issues


def main():
    print("=" * 50)
    print("🔧 University Assistant - Setup Checker")
    print("=" * 50)
    
    all_issues = []
    
    all_issues.extend(check_env())
    all_issues.extend(check_redis())
    all_issues.extend(check_openai())
    all_issues.extend(check_embeddings())
    all_issues.extend(check_resources())
    
    print("\n" + "=" * 50)
    
    if all_issues:
        print("❌ Issues Found:")
        for i, issue in enumerate(all_issues, 1):
            print(f"   {i}. {issue}")
        print("\n💡 Fix these issues to improve system reliability.")
        return 1
    else:
        print("✅ All checks passed!")
        print("\n💡 System is ready. Run: python server.py")
        return 0


if __name__ == "__main__":
    sys.exit(main())

