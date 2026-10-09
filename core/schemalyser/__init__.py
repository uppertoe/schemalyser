import logging

# sqlglot logs warnings that can quote parts of a statement. Nothing from a request may reach a log.
logging.getLogger("sqlglot").disabled = True
