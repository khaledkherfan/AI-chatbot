"""
Quick View Redis Data - Simple command-line viewer
Usage: 
    python quick_view_redis.py                    # View all
    python quick_view_redis.py registration      # View specific key
"""
import json
import sys
import os
import io
from services.redis_service import RedisService

# Fix Windows console encoding for UTF-8
if sys.platform == 'win32':
    try:
        sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
        sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')
    except:
        pass

def main():
    redis_service = RedisService()
    
    if not redis_service.is_connected():
        print("Redis not connected!")
        return
    
    client = redis_service.client
    
    # If specific key provided
    if len(sys.argv) > 1:
        canonical_key = sys.argv[1]
        print(f"\nViewing: {canonical_key}\n")
        
        # Get data
        data = redis_service.fetch_from_redis(canonical_key)
        if data:
            print("Cached Data Found:")
            json_str = json.dumps(data, indent=2, ensure_ascii=False)
            print(json_str)
        else:
            print("No cached data found for this key.")
        
        # Get aliases
        aliases = redis_service.get_aliases_for_key(canonical_key)
        if aliases:
            print(f"\nAliases ({len(aliases)}):")
            for alias in aliases:
                print(f"   - {alias}")
        else:
            print("\nNo aliases found.")
        
        return
    
    # Show all keys
    print("\n" + "=" * 80)
    print("  REDIS DATA SUMMARY")
    print("=" * 80)
    
    # Get stats
    stats = redis_service.get_stats()
    print(f"\nStatistics:")
    print(f"   Data Keys:     {stats.get('total_data_keys', 0)}")
    print(f"   Aliases:       {stats.get('total_aliases', 0)}")
    print(f"   Embeddings:    {stats.get('total_embeddings', 0)}")
    
    # List all data keys
    print(f"\nCached Data Keys:")
    cursor = 0
    count = 0
    while True:
        cursor, keys = client.scan(cursor, match="data:*", count=100)
        for key in keys:
            count += 1
            canonical_key = key.replace("data:", "")
            print(f"   {count}. {canonical_key}")
        
        if cursor == 0:
            break
    
    if count == 0:
        print("   (No cached data)")
    else:
        print(f"\nTip: Run 'python quick_view_redis.py <key>' to view details")
        print(f"   Example: python quick_view_redis.py registration")

if __name__ == "__main__":
    main()
