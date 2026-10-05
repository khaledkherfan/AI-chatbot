"""
View Redis Data - Interactive script to browse cached data
Usage: python view_redis_data.py
"""
import json
import sys
from services.redis_service import RedisService
from config import REDIS_HOST, REDIS_PORT

def print_section(title):
    """Print a formatted section header."""
    print("\n" + "=" * 80)
    print(f"  {title}")
    print("=" * 80)

def view_all_keys(redis_service):
    """View all keys in Redis."""
    if not redis_service.is_connected():
        print("❌ Redis not connected!")
        return
    
    client = redis_service.client
    
    print_section("REDIS DATA OVERVIEW")
    
    # Get all key types
    data_keys = []
    alias_keys = []
    embedding_keys = []
    canonical_keys = []
    
    try:
        # Scan for all keys
        cursor = 0
        while True:
            cursor, keys = client.scan(cursor, count=100)
            for key in keys:
                if key.startswith("data:"):
                    data_keys.append(key)
                elif key.startswith("alias:"):
                    alias_keys.append(key)
                elif key.startswith("emb:"):
                    embedding_keys.append(key)
                elif key.startswith("canonical:"):
                    canonical_keys.append(key)
            
            if cursor == 0:
                break
        
        print(f"\n📊 Statistics:")
        print(f"   Data Keys (cached JSON):     {len(data_keys)}")
        print(f"   Alias Mappings:              {len(alias_keys)}")
        print(f"   Embeddings:                   {len(embedding_keys)}")
        print(f"   Canonical Key Lists:          {len(canonical_keys)}")
        print(f"   Total Keys:                   {len(data_keys) + len(alias_keys) + len(embedding_keys) + len(canonical_keys)}")
        
    except Exception as e:
        print(f"❌ Error scanning keys: {e}")
        return
    
    return {
        'data_keys': data_keys,
        'alias_keys': alias_keys,
        'embedding_keys': embedding_keys,
        'canonical_keys': canonical_keys
    }

def view_cached_data(redis_service, canonical_key=None):
    """View cached JSON data."""
    print_section("CACHED DATA (JSON Datasets)")
    
    if not redis_service.is_connected():
        print("❌ Redis not connected!")
        return
    
    client = redis_service.client
    
    if canonical_key:
        # View specific key
        key = f"data:{canonical_key}"
        data = client.get(key)
        if data:
            try:
                json_data = json.loads(data)
                print(f"\n📁 Key: {canonical_key}")
                print(f"   URL: {json_data.get('url', 'N/A')}")
                print(f"   Title: {json_data.get('title', 'N/A')}")
                print(f"   Aliases: {len(json_data.get('aliases', []))}")
                print(f"\n   Full JSON:")
                print(json.dumps(json_data, indent=2, ensure_ascii=False))
            except Exception as e:
                print(f"❌ Error parsing JSON: {e}")
        else:
            print(f"❌ No data found for key: {canonical_key}")
    else:
        # View all data keys
        cursor = 0
        count = 0
        while True:
            cursor, keys = client.scan(cursor, match="data:*", count=100)
            for key in keys:
                count += 1
                canonical_key = key.replace("data:", "")
                data = client.get(key)
                if data:
                    try:
                        json_data = json.loads(data)
                        print(f"\n{count}. 📁 {canonical_key}")
                        print(f"   URL: {json_data.get('url', 'N/A')}")
                        print(f"   Title: {json_data.get('title', 'N/A')}")
                        print(f"   Summary: {json_data.get('summary', 'N/A')[:100]}...")
                        print(f"   Aliases: {len(json_data.get('aliases', []))}")
                    except:
                        print(f"\n{count}. 📁 {canonical_key} (parse error)")
            
            if cursor == 0:
                break
        
        if count == 0:
            print("   No cached data found.")

def view_aliases(redis_service, canonical_key=None):
    """View alias mappings."""
    print_section("ALIAS MAPPINGS")
    
    if not redis_service.is_connected():
        print("❌ Redis not connected!")
        return
    
    client = redis_service.client
    
    if canonical_key:
        # View aliases for specific key
        aliases = redis_service.get_aliases_for_key(canonical_key)
        print(f"\n📁 Canonical Key: {canonical_key}")
        print(f"   Total Aliases: {len(aliases)}")
        print(f"\n   Aliases:")
        for i, alias in enumerate(aliases, 1):
            print(f"   {i}. {alias}")
    else:
        # View all aliases
        cursor = 0
        count = 0
        alias_map = {}
        
        while True:
            cursor, keys = client.scan(cursor, match="alias:*", count=100)
            for key in keys:
                count += 1
                alias = key.replace("alias:", "")
                canonical = client.get(key)
                if canonical:
                    if canonical not in alias_map:
                        alias_map[canonical] = []
                    alias_map[canonical].append(alias)
            
            if cursor == 0:
                break
        
        print(f"\n   Total Alias Mappings: {count}")
        print(f"\n   Aliases by Canonical Key:")
        for canonical, aliases in sorted(alias_map.items()):
            print(f"\n   📁 {canonical} ({len(aliases)} aliases):")
            for alias in aliases[:10]:  # Show first 10
                print(f"      • {alias}")
            if len(aliases) > 10:
                print(f"      ... and {len(aliases) - 10} more")

def view_embeddings(redis_service):
    """View embedding statistics."""
    print_section("EMBEDDING STATISTICS")
    
    if not redis_service.is_connected():
        print("❌ Redis not connected!")
        return
    
    client = redis_service.client
    
    cursor = 0
    count = 0
    embedding_sizes = []
    
    while True:
        cursor, keys = client.scan(cursor, match="emb:*", count=100)
        for key in keys:
            count += 1
            data = client.get(key)
            if data:
                try:
                    emb_data = json.loads(data)
                    embedding = emb_data.get('embedding', [])
                    embedding_sizes.append(len(embedding))
                except:
                    pass
        
        if cursor == 0:
            break
    
    print(f"\n   Total Embeddings: {count}")
    if embedding_sizes:
        print(f"   Embedding Dimensions: {embedding_sizes[0] if embedding_sizes else 'N/A'}")
        print(f"   (All embeddings should have same dimension)")

def interactive_menu():
    """Interactive menu to browse Redis data."""
    redis_service = RedisService()
    
    if not redis_service.is_connected():
        print("❌ Redis not connected!")
        print(f"   Make sure Redis is running on {REDIS_HOST}:{REDIS_PORT}")
        return
    
    while True:
        print("\n" + "=" * 80)
        print("  REDIS DATA VIEWER")
        print("=" * 80)
        print("\n1. View All Keys (Overview)")
        print("2. View Cached Data (JSON)")
        print("3. View Aliases")
        print("4. View Embeddings Stats")
        print("5. View Specific Canonical Key")
        print("6. Exit")
        
        choice = input("\nSelect option (1-6): ").strip()
        
        if choice == "1":
            view_all_keys(redis_service)
        elif choice == "2":
            view_cached_data(redis_service)
        elif choice == "3":
            view_aliases(redis_service)
        elif choice == "4":
            view_embeddings(redis_service)
        elif choice == "5":
            key = input("Enter canonical key (e.g., 'registration', 'fees'): ").strip()
            if key:
                print("\n--- Data ---")
                view_cached_data(redis_service, key)
                print("\n--- Aliases ---")
                view_aliases(redis_service, key)
        elif choice == "6":
            print("\n👋 Goodbye!")
            break
        else:
            print("❌ Invalid option. Please try again.")

if __name__ == "__main__":
    try:
        interactive_menu()
    except KeyboardInterrupt:
        print("\n\n👋 Goodbye!")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
