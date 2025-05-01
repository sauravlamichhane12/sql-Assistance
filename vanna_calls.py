vanna_call.py 



import os
import pymysql
import chromadb
import pandas as pd
from dotenv import load_dotenv
from vanna.google import GoogleGeminiChat
from vanna.chromadb import ChromaDB_VectorStore
from sqlalchemy import create_engine
from sentence_transformers import SentenceTransformer
import json
import re
from typing import List, Dict

# Load environment variables
load_dotenv()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL")

# Validate environment variables
if not GEMINI_API_KEY or not GEMINI_MODEL:
    raise ValueError("GEMINI_API_KEY and GEMINI_MODEL must be set in the .env file")

# Database configurations
DATABASES = {
    "chinook": {
        "host": "localhost",
        "user": "root",
        "password": "root1234",
        "database": "chinook",
        "port": 3306
    },
    "college": {
        "host": "localhost",
        "user": "root",
        "password": "root1234",
        "database": "college",
        "port": 3306
    },
    "classroom": {
        "host": "localhost",
        "user": "root",
        "password": "root1234",
        "database": "classroom",
        "port": 3306
    },
    "saurav": {
        "host": "localhost",
        "user": "root",
        "password": "root1234",
        "database": "saurav",
        "port": 3306
    }
}

# Initialize embedding model
embedding_model = SentenceTransformer('paraphrase-MiniLM-L6-v2')

class MyVanna(ChromaDB_VectorStore, GoogleGeminiChat):
    def __init__(self, db_name):
        if db_name not in DATABASES:
            raise ValueError(f"Database '{db_name}' not found in configuration")
        
        self.db_config = DATABASES[db_name].copy()
        self.db_config['database'] = db_name
        self.db_name = db_name
        
        persist_directory = f"./chroma_db/{db_name}"
        os.makedirs(persist_directory, exist_ok=True)
        chroma_client = chromadb.PersistentClient(path=persist_directory)
        ChromaDB_VectorStore.__init__(self, config={'client': chroma_client})
        GoogleGeminiChat.__init__(self, config={'api_key': GEMINI_API_KEY, 'model': GEMINI_MODEL})
        self.schema_collection = chroma_client.get_or_create_collection(f"{db_name}_schema")
    
    def get_embedding(self, text: str) -> List[float]:
        """Generate an embedding for the given text using Sentence Transformers."""
        return embedding_model.encode(text, show_progress_bar=False).tolist()

    def store_database_schema(self):
        try:
            db_config = DATABASES[self.db_name]
            connection = pymysql.connect(
                host=db_config["host"],
                user=db_config["user"],
                password=db_config["password"],
                database=self.db_name,
                port=db_config["port"]
            )
            cursor = connection.cursor()

            cursor.execute("SHOW TABLES")
            tables = cursor.fetchall()

            for (table_name,) in tables:
                cursor.execute(f"""
                    SELECT COLUMN_NAME
                    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
                    WHERE TABLE_SCHEMA = '{self.db_name}' AND TABLE_NAME = '{table_name}' AND CONSTRAINT_NAME = 'PRIMARY'
                """)
                primary_keys = [row[0] for row in cursor.fetchall()]

                cursor.execute(f"""
                    SELECT COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
                    FROM INFORMATION_SCHEMA.KEY_COLUMN_USAGE
                    WHERE TABLE_SCHEMA = '{self.db_name}' AND TABLE_NAME = '{table_name}' AND REFERENCED_TABLE_NAME IS NOT NULL
                """)
                foreign_keys = {row[0]: f"{row[1]}({row[2]})" for row in cursor.fetchall()}

                cursor.execute(f"""
                    SELECT INDEX_NAME, COLUMN_NAME, NON_UNIQUE 
                    FROM INFORMATION_SCHEMA.STATISTICS 
                    WHERE TABLE_NAME = '{table_name}' AND TABLE_SCHEMA = '{self.db_name}'
                """)
                indexes = [{"index_name": row[0], "column": row[1], "is_unique": not bool(row[2])} for row in cursor.fetchall()]

                cursor.execute(f"""
                    SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, EXTRA
                    FROM INFORMATION_SCHEMA.COLUMNS
                    WHERE TABLE_NAME = '{table_name}' AND TABLE_SCHEMA = '{self.db_name}'
                """)
                columns = cursor.fetchall()

                schema = {
                    "table_name": table_name,
                    "columns": [
                        {
                            "column_name": col[0],
                            "data_type": col[1],
                            "is_primary_key": col[0] in primary_keys,
                            "is_foreign_key": col[0] in foreign_keys,
                            "foreign_key_reference": foreign_keys.get(col[0], None),
                            "is_nullable": col[2] == "YES",
                            "default_value": col[3],
                            "extra_info": col[4]
                        }
                        for col in columns
                    ],
                    "primary_keys": primary_keys,
                    "foreign_keys": list(foreign_keys.keys()),
                    "indexes": indexes,
                    "table_description": f"Table {table_name} in database {self.db_name}",
                    "relationships": foreign_keys,
                    "index_details": indexes
                }

                embedding_text = f"{table_name} {schema['table_description']}"
                schema_json = json.dumps(schema)
                schema_embedding = self.get_embedding(embedding_text)

                existing_docs = self.schema_collection.get(ids=[table_name])
                if not existing_docs["documents"]:
                    self.schema_collection.add(
                        ids=[table_name],
                        embeddings=[schema_embedding],
                        documents=[schema_json],
                        metadatas=[{"db_name": self.db_name}]
                    )
                    print(f"✅ Schema for table '{table_name}' stored in ChromaDB.")

            cursor.close()
            connection.close()

        except Exception as e:
            print(f"❌ Error while storing database schema for {self.db_name}: {e}")

    def _extract_table_names(self, query: str) -> List[str]:
        """Extract table names from the user query and validate against schema.

        Args:
            query (str): The user query in natural language.

        Returns:
            List[str]: A list of valid table names mentioned explicitly or implicitly.
        """
        if not query or not query.strip():
            print("⚠️ Empty query provided.")
            return []

        query_lower = query.lower()
        collection_data = self.schema_collection.get()
        table_names = collection_data.get("ids", [])
        if not table_names:
            print("⚠️ No tables found in schema collection.")
            return []

        mentioned_tables = []

        # Regex for explicit table mentions in FROM/JOIN clauses
        pattern = r'\b(?:from|join)\s+([A-Za-z_][A-Za-z0-9_]*)\b'
        explicit_matches = re.findall(pattern, query_lower, re.IGNORECASE)
        for table in explicit_matches:
            if table in table_names:
                mentioned_tables.append(table)
            else:
                print(f"⚠️ Table '{table}' mentioned but not found in schema.")

        # Keyword matching for table names and variations
        for table in table_names:
            table_lower = table.lower()
            table_variations = [table_lower, table_lower.rstrip('s'), table_lower + 's']
            for variation in table_variations:
                if re.search(rf'\b{variation}\b', query_lower) and table not in mentioned_tables:
                    mentioned_tables.append(table)
                    break

        return list(set(mentioned_tables))

    def _schema_to_natural_language(self, schema: Dict) -> str:
        """Convert a schema JSON to a concise natural-language description."""
        table_name = schema["table_name"]
        description = f"Table: {table_name}\nColumns:\n"
        for col in schema["columns"]:
            col_desc = f"- {col['column_name']} ({col['data_type']})"
            if col["is_primary_key"]:
                col_desc += ", Primary Key"
            if col["is_foreign_key"]:
                col_desc += f", Foreign Key to {col['foreign_key_reference']}"
            if col["is_nullable"]:
                col_desc += ", Nullable"
            description += f"{col_desc}\n"
        
        if schema["relationships"]:
            description += "Relationships:\n"
            for fk_col, ref in schema["relationships"].items():
                description += f"- {fk_col} references {ref}\n"
        
        return description.strip()

    def _select_relevant_schemas(self, user_query: str, top_k: int = 5) -> List[Dict]:
        """Select relevant schemas, handling missing tables with vector search fallback.

        Args:
            user_query (str): The user query in natural language.
            top_k (int): Maximum number of schemas to return.

        Returns:
            List[Dict]: List of schema dictionaries relevant to the query.
        """
        try:
            schemas = []
            explicit_tables = self._extract_table_names(user_query)

            # Step 1: Retrieve schemas for explicitly mentioned tables
            if explicit_tables:
                for table in explicit_tables:
                    schema_doc = self.schema_collection.get(ids=[table], include=["documents"])
                    if schema_doc["documents"]:
                        schemas.append(json.loads(schema_doc["documents"][0]))
                    else:
                        print(f"⚠️ Explicit table '{table}' not found, relying on vector search.")

            # Step 2: Supplement with vector search to meet top_k
            remaining_slots = top_k - len(schemas)
            if remaining_slots > 0:
                user_query_embedding = self.get_embedding(user_query)
                results = self.schema_collection.query(
                    query_embeddings=[user_query_embedding],
                    n_results=remaining_slots,
                    include=["documents"]
                )
                for doc in results["documents"][0] if results["documents"] else []:
                    schema = json.loads(doc)
                    if schema["table_name"] not in [s["table_name"] for s in schemas]:
                        schemas.append(schema)

            # Step 3: Include related tables via foreign keys
            final_schemas = schemas.copy()
            for schema in schemas:
                for col in schema["columns"]:
                    if col["is_foreign_key"] and col["foreign_key_reference"]:
                        ref_table = col["foreign_key_reference"].split("(")[0]
                        if ref_table not in [s["table_name"] for s in final_schemas]:
                            ref_schema = self.schema_collection.get(ids=[ref_table], include=["documents"])
                            if ref_schema["documents"]:
                                final_schemas.append(json.loads(ref_schema["documents"][0]))

            if not final_schemas:
                print("⚠️ No relevant schemas found, returning empty list.")
            return final_schemas[:top_k]
        except Exception as e:
            print(f"Error selecting relevant schemas: {e}")
            return []

    def generate_sql_query(self, user_query: str) -> str:
        """Generate an SQL query using filtered schema context."""
        try:
            if not user_query or not user_query.strip():
                return "Error: Empty query provided."

            # Select relevant schemas
            schemas = self._select_relevant_schemas(user_query, top_k=5)
            if not schemas:
                return "Error: No relevant schema found in ChromaDB."

            # Convert schemas to natural language
            schema_descriptions = [self._schema_to_natural_language(schema) for schema in schemas]
            full_schema = "\n\n".join(schema_descriptions)

            # Build the prompt
            full_prompt = f"""
            You are an SQL expert tasked with generating an SQL query based on a user question and a database schema.

            Database Schema:
            {full_schema}

            User Question:
            {user_query}

            Instructions:
            - Analyze the schema to understand the table structures and relationships.
            - Use only the tables and columns relevant to the question.
            - Include appropriate JOINs for foreign key relationships.
            - Write a valid, concise, and optimized SQL query for MySQL.
            - Avoid unnecessary subqueries or joins for performance.
            - Use exact column names from the schema.
            - If the question is ambiguous, make reasonable assumptions based on the schema.
            - For ranking or limiting results, use LIMIT for MySQL.
            - Ensure the query is syntactically correct and handles edge cases (e.g., NULL values).
            """
            # Generate SQL
            sql_query = self.generate_sql(full_prompt, allow_llm_to_see_data=True)
            return sql_query.strip()
        except Exception as e:
            print(f"Error generating SQL query: {e}")
            return "Error: Failed to generate SQL query."

    def connect_to_database(self):
        """Establish a connection to the database."""
        try:
            if 'database' not in self.db_config:
                self.db_config['database'] = self.db_name
            connection = pymysql.connect(
                host=self.db_config['host'],
                user=self.db_config['user'],
                password=self.db_config['password'],
                database=self.db_config['database'],
                port=self.db_config['port']
            )
            return connection
        except Exception as e:
            print(f"⚠️ Failed to connect to database {self.db_name}: {e}")
            raise ConnectionError(f"Failed to connect to the database: {e}")

    def fetch_schema_information(self, connection):
        """Fetch schema information from the database."""
        try:
            sql_query = f"""
                SELECT *
                FROM INFORMATION_SCHEMA.COLUMNS
                WHERE TABLE_SCHEMA = '{self.db_name}'
            """
            return pd.read_sql(sql_query, connection)
        except Exception as e:
            print(f"⚠️ Error fetching schema information for {self.db_name}: {e}")
            raise ValueError(f"Error fetching schema information: {e}")

    def generate_training_plan(self, df_information_schema):
        """Generate the training plan based on schema information."""
        try:
            if df_information_schema.empty:
                raise ValueError("Schema information is empty")
            return self.get_training_plan_generic(df_information_schema)
        except IndexError as ie:
            print(f"❌ Index Error in training plan: {ie}")
            raise IndexError(f"Index error in training plan: {ie}")
        except Exception as e:
            print(f"❌ Error generating training plan: {e}")
            raise RuntimeError(f"Error generating training plan: {e}")

    def execute_training(self, plan):
        """Execute the training process using the generated plan."""
        try:
            if not plan:
                raise ValueError("Training plan is empty")
            self.train(plan=plan)
            print("✅ Training Completed Successfully!")
        except Exception as e:
            print(f"❌ Error during training: {e}")
            raise RuntimeError(f"Error during training: {e}")

    def train_vanna(self):
        """Main method to handle the training process."""
        connection = None
        try:
            print(f"🚀 Starting training for database: {self.db_name}")
            connection = self.connect_to_database()
            df_information_schema = self.fetch_schema_information(connection)
            plan = self.generate_training_plan(df_information_schema)
            print("📌 Raw Training Plan:", plan)
            self.execute_training(plan)
        except Exception as e:
            print(f"❌ Error during training: {e}")
            raise
        finally:
            if connection:
                connection.close()