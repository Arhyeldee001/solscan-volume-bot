# test_solders.py
try:
    from solders.pubkey import Pubkey 
    print("✅ Import successful!")
    
    # Test creating a pubkey
    test_key = Pubkey.from_string("3UrQziUTpj5YtUAqncDqwJ44nFSB6pmHshkof3FdFqg3")
    print(f"✅ Successfully created pubkey: {test_key}")
    
except Exception as e:
    print(f"❌ Error: {e}")