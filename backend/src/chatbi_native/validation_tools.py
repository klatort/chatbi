"""
Validation Tools for Superset Operations
=========================================
Tools to validate parameters before executing operations to prevent errors.
"""

import logging
from typing import List, Optional, Dict, Any
from langchain_core.tools import tool
from pydantic import BaseModel, Field

from chatbi_native.mcp_client import run_mcp_tool
from chatbi_native.config import Config
from chatbi_native.user_context import get_user_context

logger = logging.getLogger(__name__)

# ── Validation Tools ───────────────────────────────────────────────────────

class ValidateChartParametersSchema(BaseModel):
    datasource_id: int = Field(
        ...,
        description="The exact integer ID of the dataset."
    )
    viz_type: str = Field(
        ...,
        description="The chart type. Must be a valid Superset visualization type."
    )
    metrics: List[str] = Field(
        ...,
        description="A flat array of strings representing the metrics. Example: ['count', 'sum__amount']"
    )
    groupby: Optional[List[str]] = Field(
        default=[],
        description="A flat array of strings representing columns to group by. Example: ['country_name']"
    )

@tool("validate_chart_parameters", args_schema=ValidateChartParametersSchema)
def validate_chart_parameters(datasource_id: int, viz_type: str, metrics: List[str], 
                             groupby: Optional[List[str]] = None):
    """
    Validate chart parameters before creation.
    Checks for required fields, correct data types, and valid values.
    Returns validation results with any errors found.
    """
    errors = []
    warnings = []
    
    # Check datasource_id
    if not isinstance(datasource_id, int) or datasource_id <= 0:
        errors.append("datasource_id must be a positive integer")
    
    # Check metrics
    if not isinstance(metrics, list):
        errors.append("metrics must be a list")
    elif not metrics:
        errors.append("metrics cannot be empty")
    elif not all(isinstance(m, str) for m in metrics):
        errors.append("all metrics must be strings")
    
    # Check groupby
    if groupby is not None and not isinstance(groupby, list):
        errors.append("groupby must be a list or None")
    elif groupby and not all(isinstance(g, str) for g in groupby):
        errors.append("all groupby values must be strings")
    
    # Check viz_type
    valid_viz_types = ["echarts_timeseries", "pie", "big_number_total", "table", "bar", 
                      "line", "area", "scatter", "bubble", "heatmap", "box_plot"]
    if viz_type not in valid_viz_types:
        warnings.append(f"viz_type '{viz_type}' is not in the recommended list: {', '.join(valid_viz_types[:5])}...")
    
    # Check if metrics contain valid column names (basic check)
    for metric in metrics:
        if not metric or not isinstance(metric, str):
            errors.append(f"Invalid metric: {metric}")
        elif metric.strip() == "":
            errors.append("Metric cannot be empty string")
    
    if errors:
        return {
            "valid": False,
            "errors": errors,
            "warnings": warnings,
            "message": f"Validation failed: {', '.join(errors)}"
        }
    
    return {
        "valid": True,
        "warnings": warnings,
        "message": "Parameters are valid for chart creation" + 
                  (f" (warnings: {', '.join(warnings)})" if warnings else "")
    }


class ValidateDatasetAccessSchema(BaseModel):
    datasource_id: int = Field(
        ...,
        description="The exact integer ID of the dataset to check access for."
    )
    user_id: str = Field(
        default="anonymous",
        description="User ID for permission checking."
    )

@tool("validate_dataset_access", args_schema=ValidateDatasetAccessSchema)
def validate_dataset_access(datasource_id: int, user_id: str = "anonymous"):
    """
    Validate that the user has access to the specified dataset.
    Checks cached user datasets first, then falls back to API if needed.
    """
    user_context = get_user_context(user_id)
    
    # Check cached datasets
    if user_context.accessible_datasets:
        for dataset in user_context.accessible_datasets:
            if isinstance(dataset, dict) and dataset.get('id') == datasource_id:
                return {
                    "has_access": True,
                    "dataset_name": dataset.get('table_name') or dataset.get('name', 'Unknown'),
                    "source": "cache",
                    "message": f"User has access to dataset '{dataset.get('table_name') or dataset.get('name', 'Unknown')}'"
                }
            elif hasattr(dataset, 'id') and dataset.id == datasource_id:
                name = getattr(dataset, 'table_name', None) or getattr(dataset, 'name', 'Unknown')
                return {
                    "has_access": True,
                    "dataset_name": name,
                    "source": "cache",
                    "message": f"User has access to dataset '{name}'"
                }
    
    # Not in cache, check via API
    try:
        # Try to get dataset schema - if successful, user has access
        result = run_mcp_tool(Config.MCP_SERVER_URL, "get_dataset_schema", {"datasource_id": datasource_id})
        return {
            "has_access": True,
            "dataset_name": "Verified via API",
            "source": "api",
            "message": f"User has access to dataset ID {datasource_id}"
        }
    except Exception as e:
        if "not found" in str(e).lower() or "does not exist" in str(e).lower():
            return {
                "has_access": False,
                "error": str(e),
                "message": f"Dataset {datasource_id} does not exist"
            }
        elif "permission" in str(e).lower() or "access" in str(e).lower():
            return {
                "has_access": False,
                "error": str(e),
                "message": f"User does not have permission to access dataset {datasource_id}"
            }
        else:
            return {
                "has_access": False,
                "error": str(e),
                "message": f"Error checking dataset access: {str(e)}"
            }


class ValidateSqlQuerySchema(BaseModel):
    query: str = Field(
        ...,
        description="The SQL query to validate."
    )
    database_id: int = Field(
        ...,
        description="The ID of the database to validate the query against."
    )

@tool("validate_sql_query", args_schema=ValidateSqlQuerySchema)
def validate_sql_query(query: str, database_id: int):
    """
    Validate a SQL query for syntax and basic safety.
    Returns validation results with any errors or warnings.
    """
    errors = []
    warnings = []
    
    # Basic SQL validation
    if not query or not query.strip():
        errors.append("Query cannot be empty")
    
    # Check for potentially dangerous operations (basic safety check)
    query_lower = query.lower()
    dangerous_operations = ["drop ", "delete ", "truncate ", "update ", "alter ", "grant ", "revoke "]
    
    for op in dangerous_operations:
        if op in query_lower:
            warnings.append(f"Query contains '{op.strip()}' operation - ensure this is intentional")
    
    # Check for basic SQL structure
    if not any(keyword in query_lower for keyword in ["select ", "with ", "explain ", "describe "]):
        if not any(op in query_lower for op in dangerous_operations):
            warnings.append("Query does not appear to be a SELECT statement")
    
    # Check for missing semicolon (not required but good practice)
    if not query.strip().endswith(';'):
        warnings.append("Query does not end with a semicolon")
    
    if errors:
        return {
            "valid": False,
            "errors": errors,
            "warnings": warnings,
            "message": f"SQL validation failed: {', '.join(errors)}"
        }
    
    return {
        "valid": True,
        "warnings": warnings,
        "message": "SQL query appears valid" + 
                  (f" (warnings: {', '.join(warnings)})" if warnings else "")
    }


class ValidateDashboardParametersSchema(BaseModel):
    dashboard_title: str = Field(
        ...,
        description="Title for the new dashboard."
    )
    css: Optional[str] = Field(
        default=None,
        description="Optional CSS for the dashboard."
    )
    slug: Optional[str] = Field(
        default=None,
        description="Optional slug for the dashboard URL."
    )

@tool("validate_dashboard_parameters", args_schema=ValidateDashboardParametersSchema)
def validate_dashboard_parameters(dashboard_title: str, css: Optional[str] = None, 
                                 slug: Optional[str] = None):
    """
    Validate dashboard creation parameters.
    """
    errors = []
    warnings = []
    
    # Check title
    if not dashboard_title or not dashboard_title.strip():
        errors.append("Dashboard title cannot be empty")
    elif len(dashboard_title) > 200:
        warnings.append("Dashboard title is very long (max 200 characters recommended)")
    
    # Check CSS (if provided)
    if css and len(css) > 5000:
        warnings.append("CSS is very long (max 5000 characters recommended)")
    
    # Check slug (if provided)
    if slug:
        if not slug.isidentifier() and not all(c.isalnum() or c in '-_' for c in slug):
            errors.append("Slug can only contain alphanumeric characters, hyphens, and underscores")
        if len(slug) > 100:
            warnings.append("Slug is very long (max 100 characters recommended)")
    
    if errors:
        return {
            "valid": False,
            "errors": errors,
            "warnings": warnings,
            "message": f"Dashboard validation failed: {', '.join(errors)}"
        }
    
    return {
        "valid": True,
        "warnings": warnings,
        "message": "Dashboard parameters are valid" + 
                  (f" (warnings: {', '.join(warnings)})" if warnings else "")
    }


# ── Helper Functions ───────────────────────────────────────────────────

def get_validation_tools() -> List:
    """
    Get all validation tools.
    
    Returns:
        List of validation tool objects
    """
    return [
        validate_chart_parameters,
        validate_dataset_access,
        validate_sql_query,
        validate_dashboard_parameters,
    ]


def validate_before_execute(operation: str, parameters: Dict[str, Any], user_id: str = "anonymous") -> Dict[str, Any]:
    """
    High-level validation function that routes to appropriate validation tool.
    
    Args:
        operation: Type of operation ('chart', 'dataset_access', 'sql', 'dashboard')
        parameters: Parameters for the operation
        user_id: User ID for permission checks
    
    Returns:
        Validation result dictionary
    """
    if operation == "chart":
        required = ["datasource_id", "viz_type", "metrics"]
        missing = [p for p in required if p not in parameters]
        if missing:
            return {
                "valid": False,
                "errors": [f"Missing required parameters: {', '.join(missing)}"],
                "message": f"Missing required parameters for chart validation"
            }
        
        return validate_chart_parameters.invoke({
            "datasource_id": parameters["datasource_id"],
            "viz_type": parameters["viz_type"],
            "metrics": parameters["metrics"],
            "groupby": parameters.get("groupby", [])
        })
    
    elif operation == "dataset_access":
        if "datasource_id" not in parameters:
            return {
                "valid": False,
                "errors": ["Missing required parameter: datasource_id"],
                "message": "Missing datasource_id for dataset access validation"
            }
        
        return validate_dataset_access.invoke({
            "datasource_id": parameters["datasource_id"],
            "user_id": user_id
        })
    
    elif operation == "sql":
        required = ["query", "database_id"]
        missing = [p for p in required if p not in parameters]
        if missing:
            return {
                "valid": False,
                "errors": [f"Missing required parameters: {', '.join(missing)}"],
                "message": f"Missing required parameters for SQL validation"
            }
        
        return validate_sql_query.invoke({
            "query": parameters["query"],
            "database_id": parameters["database_id"]
        })
    
    elif operation == "dashboard":
        if "dashboard_title" not in parameters:
            return {
                "valid": False,
                "errors": ["Missing required parameter: dashboard_title"],
                "message": "Missing dashboard_title for dashboard validation"
            }
        
        return validate_dashboard_parameters.invoke({
            "dashboard_title": parameters["dashboard_title"],
            "css": parameters.get("css"),
            "slug": parameters.get("slug")
        })
    
    else:
        return {
            "valid": False,
            "errors": [f"Unknown operation type: {operation}"],
            "message": f"Unknown operation type: {operation}"
        }