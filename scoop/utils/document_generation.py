# documentation/pydantic_doc_generator.py

from pathlib import Path
import importlib.util
import inspect, types, importlib, sys
from pydantic_core import PydanticUndefined
from typing import Any, get_args, get_origin, List, Optional, Dict, Union
from pydantic import BaseModel
from pydantic.fields import FieldInfo
from jinja2 import Template


def resolve_model_class(class_name: str):
    """
    Resolve a class name into an actual Pydantic class.

    Priority:
    1. scoop.schema.<ClassName>
    2. Any already-imported module containing <ClassName>
    3. Return None (caller can treat it as non-nested)
    """
    # 1. Try the expected target location: scoop.schema.<ClassName>
    try:
        schema_mod = importlib.import_module("scoop.schema")
        if hasattr(schema_mod, class_name):
            cls = getattr(schema_mod, class_name)
            if isinstance(cls, type) and issubclass(cls, BaseModel):
                return cls
    except Exception:
        pass

    # 2. Search all currently-loaded modules
    for module in sys.modules.values():
        if module is None:
            continue
        try:
            if hasattr(module, class_name):
                cls = getattr(module, class_name)
                if isinstance(cls, type) and issubclass(cls, BaseModel):
                    return cls
        except Exception:
            continue

    # 3. Not found anywhere
    return None


def discover_models(module_path: Path) -> Dict[str, Any]:
    """Load a module file and return all Pydantic classes defined in it."""
    spec = importlib.util.spec_from_file_location(module_path.stem, module_path)
    module = importlib.util.module_from_spec(spec) # type: ignore
    spec.loader.exec_module(module) # type: ignore

    models = {}
    for name, obj in module.__dict__.items():
        if inspect.isclass(obj) and issubclass(obj, BaseModel):
            # Exclude Pydantic built-ins
            if obj.__module__ == module.__name__:
                models[name] = obj

    return models

def unwrap_annotation(annotation):
    """
    Remove Optional, List, Union[...] wrappers until the core type emerges.
    Returns the underlying type.
    """
    origin = get_origin(annotation)

    # Optional[T] or Union[T, None]
    if origin in (Union, types.UnionType, list, List):
        args = [unwrap_annotation(a) for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
        else:
            return Union[tuple(args)]  # re-wrap if multiple types remain

    return annotation

def detect_pydantic_model(annotation):
    """
    Returns (True, ModelClass) if the annotation refers to a Pydantic model,
    even if wrapped in Optional/List/etc.
    """
    base = unwrap_annotation(annotation)

    # handles fully imported classes, Unions, Optionals, Lists, etc.
    if isinstance(base, type) and issubclass(base, BaseModel):
        return True, base

    return False, None

def human_type(annotation):
    """Convert Python typing annotation into a readable form."""
    origin = get_origin(annotation)
    if origin is list or origin is List:
        t = get_args(annotation)[0]
        return f"List[{human_type(t)}]"
    if origin is Optional:
        t = get_args(annotation)[0]
        return f"Optional[{human_type(t)}]"
    if hasattr(annotation, "__name__"):
        return annotation.__name__
    return str(annotation)

def get_default(field: FieldInfo) -> Any:
    """Get the default value of a field, handling default factories."""
    if callable(field.default_factory):
        default_factory = field.default_factory
        default = f'{inspect.getmodule(default_factory).__name__}.{default_factory.__name__}()' # type: ignore
        return default
    if field.default is not PydanticUndefined:
        return field.default
    return None

def extract_field_info(name: str, field: FieldInfo) -> Dict[str, Any]:
    """Summarise field properties."""
    annotation = field.annotation
    is_model, target_model = detect_pydantic_model(annotation)
    return {
        "name": name,
        "type": human_type(annotation),
        "required": field.is_required(),
        "default": get_default(field),
        "description": field.description or "",
        "is_pydantic_model": is_model,
        "pydantic_target": target_model.__name__ if target_model else None,
    }

def extract_validators(cls):
    validators = list(cls.model_validators.keys())
    return validators

def extract_transformations(cls):
    """Look for custom transformation dictionaries like __base_transformations__."""
    if hasattr(cls, "__base_transformations__"):
        return cls.__base_transformations__
    return {}

def walk_model(model: BaseModel, visited=None):
    if visited is None:
        visited = set()

    name = model.__name__
    if name in visited:
        return []

    visited.add(name)

    fields = [
        extract_field_info(n, f)
        for n, f in model.model_fields.items()
    ]

    result = [(name, fields)]

    # recursively process nested models
    for f in fields:
        if f["is_pydantic_model"]:
            nested_cls = f["pydantic_target"]
            nested_model = resolve_model_class(nested_cls)
            if nested_model:
                result.extend(walk_model(nested_model, visited)) # type: ignore

    return result


def generate_full_mermaid_erd(walk_results: list[tuple[str, list[dict]]]) -> str:
    """
    Given walk_model results:
        [ (ModelName, [field_dicts...]), ... ]
    produce a consolidated Mermaid ER diagram.
    """

    lines = ["erDiagram"]

    # Track which classes have been rendered
    rendered_tables = set()
    relationships = []

    # --- PASS 1: Render all tables with scalar fields ---
    for model_name, fields in walk_results:

        if model_name in rendered_tables:
            continue
        
        rendered_tables.add(model_name)
        lines.append(f"    {model_name} {{")

        # Render scalar fields only
        for f in fields:
            if not f["is_pydantic_model"]:
                t = f["type"]
                mermaid_type = (
                    "int" if "int" in t else
                    "float" if "float" in t else
                    "string"
                )
                lines.append(f"        {mermaid_type} {f['name']}")

        lines.append("    }")

    # --- PASS 2: Collect relationships and ensure target tables exist ---
    for model_name, fields in walk_results:
        for f in fields:
            if f["is_pydantic_model"]:
                origin = model_name
                target = f["pydantic_target"]
                field = f["name"]

                relationships.append((origin, target, field))

                # Ensure we have a stub for target model if it was not in walk_results
                if target not in rendered_tables:
                    rendered_tables.add(target)
                    lines.append(f"    {target} {{")
                    lines.append("        string id")
                    lines.append("    }")

    # --- PASS 3: Render relationship edges ---
    for origin, target, field in relationships:
        # n:1 cardinality assumption for now (refinement later)
        lines.append(f"    {origin} ||--|| {target} : {field}")

    return "\n".join(lines)



def render_markdown(model_name: str, fields, validators, transforms) -> str:
    """Render documentation using an embedded template."""
    template = Template(
        """
        # {{ model_name }}

        ## Fields
        | Name | Type | Required | Default | Description |
        |------|------|----------|----------|-------------|
        {% for f in fields %}
        | {{ f.name }} | {{ f.type }} | {{ "✓" if f.required else "" }} | {{ f.default }} | {{ f.description }} |
        {% endfor %}

        ## Validators
        {% if validators %}
        The following validators are defined:

        {% for v in validators %}
        - `{{ v }}`
        {% endfor %}
        {% else %}
        _No validators defined._
        {% endif %}

        ## Transformation Logic
        {% if transforms %}
        Custom transformation rules:

        ```yaml
        {{ transforms | tojson(indent=2) }}
        {% else %}
        No transformation metadata found.
        {% endif %}
        """
        )
    return template.render(
        model_name=model_name,
        fields=fields,
        validators=validators,
        transforms=transforms
    )

def generate_docs(schema_dir: Path, output_dir: Path):
    output_dir.mkdir(parents=True, exist_ok=True)
    for pyfile in schema_dir.glob("*.py"):
        models = discover_models(pyfile)
        for name, model in models.items():
            fields = [
                extract_field_info(n, f)
                for n, f in model.model_fields.items()
            ]
            validators = extract_validators(model)
            transforms = extract_transformations(model)

            md = render_markdown(name, fields, validators, transforms)

            out = output_dir / f"{name}.md"
            out.write_text(md)
            print(f"✓ wrote {out}")


def generate_mermaid_class_diagram(walk_results: list[tuple[str, list[dict]]]) -> str:
    """
    Generate a Mermaid classDiagram from walk_model results.
    Produces:
      - class definitions (attributes only, no methods yet)
      - association links for nested Pydantic models
    """
    lines = ["classDiagram"]

    rendered_classes = set()
    associations = []

    # --- PASS 1: Class definitions ---
    for model_name, fields in walk_results:

        if model_name not in rendered_classes:
            rendered_classes.add(model_name)

            # Start class block
            lines.append(f"    class {model_name} {{")

            for f in fields:
                if not f["is_pydantic_model"]:
                    # Clean type for readability
                    t = f["type"].replace(" | None", "?").replace("Optional[", "").replace("]", "")
                    default = f["default"]
                    default_part = f" = {default}" if default is not None else ""

                    lines.append(f"        {t} {f['name']}{default_part}")

            lines.append("    }")

    # --- PASS 2: Associations (nested Pydantic models) ---
    for model_name, fields in walk_results:
        for f in fields:
            if f["is_pydantic_model"]:
                origin = model_name
                target = f["pydantic_target"]
                field_name = f["name"]

                # store association (avoid duplicates)
                associations.append((origin, target, field_name))

                # ensure referenced class exists
                if target not in rendered_classes:
                    rendered_classes.add(target)
                    lines.append(f"    class {target} {{")
                    lines.append("        <<pydantic>>")
                    lines.append("    }}")

    # --- PASS 3: Render association edges ---
    for origin, target, field in associations:
        # Class diagram syntax: origin --> target : field_name
        lines.append(f"    {origin} --> {target} : {field}")

    return "\n".join(lines)
