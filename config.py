import os
import yaml
from dotenv import load_dotenv
from cryptography.fernet import Fernet
import logging
from typing import Dict, Optional

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Load environment variables
load_dotenv()

class DatabaseConfig:
    def __init__(self):
        # Generate a key for encryption if not exists
        self.key = os.getenv('ENCRYPTION_KEY')
        if not self.key:
            self.key = Fernet.generate_key()
            os.environ['ENCRYPTION_KEY'] = self.key.decode()
        self.fernet = Fernet(self.key)
        
        # Load YAML configuration
        try:
            with open('config.yml', 'r') as file:
                self.config = yaml.safe_load(file)
        except Exception as e:
            logger.error(f"Error loading config.yml: {str(e)}")
            raise

    def _encrypt(self, value: str) -> str:
        """Encrypt a string value."""
        return self.fernet.encrypt(value.encode()).decode()

    def _decrypt(self, encrypted_value: str) -> str:
        """Decrypt an encrypted string value."""
        return self.fernet.decrypt(encrypted_value.encode()).decode()

    def get_database_config(self, db_name: str) -> Dict:
        """Get database configuration with decrypted credentials."""
        try:
            if db_name not in self.config['databases']:
                raise ValueError(f"Database '{db_name}' not found in config.yml")
            
            # Get base configuration from YAML
            config = self.config['databases'][db_name].copy()
            
            # Get encrypted credentials from environment variables
            encrypted_user = os.getenv(f"{db_name}_USER")
            encrypted_password = os.getenv(f"{db_name}_PASSWORD")

            if encrypted_user and encrypted_password:
                config["user"] = self._decrypt(encrypted_user)
                config["password"] = self._decrypt(encrypted_password)
            else:
                # If no encrypted credentials, use the ones from YAML
                logger.warning(f"Using unencrypted credentials for {db_name} from config.yml")
                config["user"] = self.config['databases'][db_name]["user"]
                config["password"] = self.config['databases'][db_name]["password"]

            return config
        except Exception as e:
            logger.error(f"Error getting database config for {db_name}: {str(e)}")
            raise

    def set_database_config(self, db_name: str, user: str, password: str) -> None:
        """Set database configuration with encrypted credentials."""
        try:
            if db_name not in self.config['databases']:
                raise ValueError(f"Database '{db_name}' not found in config.yml")
            
            # Encrypt credentials
            encrypted_user = self._encrypt(user)
            encrypted_password = self._encrypt(password)

            # Store in environment variables
            os.environ[f"{db_name}_USER"] = encrypted_user
            os.environ[f"{db_name}_PASSWORD"] = encrypted_password

            logger.info(f"Database credentials set for {db_name}")
        except Exception as e:
            logger.error(f"Error setting database config for {db_name}: {str(e)}")
            raise

# Initialize database configuration manager
db_config = DatabaseConfig()

# Encrypt and store all database configurations
for db_name, config in db_config.config['databases'].items():
    try:
        db_config.set_database_config(db_name, config["user"], config["password"])
    except Exception as e:
        logger.error(f"Failed to set config for {db_name}: {str(e)}") 