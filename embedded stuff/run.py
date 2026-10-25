import sqlite3
import sys

# Connect to an in-memory database for testing (changes disappear when closed)
# Replace ":memory:" with "my_database.db" to save to a physical file
DB_NAME = "accounts.db"

print(f"--- SQLite Interactive Terminal ({DB_NAME}) ---")
print("Type your SQL commands. Type 'exit' to quit.\n")

with sqlite3.connect(DB_NAME) as conn:
    cursor = conn.cursor()
    
    while True:
        try:
            # 1. Get the SQL command from the user
            sql_command = input("sqlite> ").strip()
            
            # Exit condition
            if sql_command.lower() in ('exit', 'exit;'):
                print("Goodbye!")
                break
            
            if not sql_command:
                continue
                
            # 2. Execute the command
            cursor.execute(sql_command)
            
            # 3. Handle the output if it's a query (like SELECT)
            if cursor.description:
                # Fetch column names
                headers = [description[0] for description in cursor.description]
                print(" | ".join(headers))
                print("-" * (len(" | ".join(headers)) + 4))
                
                # Fetch and print row data
                rows = cursor.fetchall()
                if not rows:
                    print("(No rows returned)")
                for row in rows:
                    print(" | ".join(str(val) for val in row))
            else:
                # For INSERT, UPDATE, DELETE, CREATE changes need to be saved
                conn.commit()
                print(f"Command executed successfully. Rows affected: {cursor.rowcount}")
                
            print() # Print a blank line for readability
            
        except sqlite3.Error as e:
            # Catch and display SQL syntax or operational errors gently
            print(f"SQL Error: {e}\n", file=sys.stderr)
            
        except (KeyboardInterrupt, EOFError):
            print("\nExiting...")
            break
