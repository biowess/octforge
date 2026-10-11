import pytest
from backend.app.schema import AnnotationItem, SceneConfig, AnnotationSet, ExportConfig, ExportRequest, CompositionConfig, PanelConfig
from backend.app.composition import compose_figure
from backend.app.export import generate_svg_export, export_artifact
import json
from PIL import Image

def test_annotation_schema_roundtrip():
    # Boxed text
    data = {
        "id": "box_1",
        "type": "text",
        "x": 10.5,
        "y": 20.0,
        "width": 100.0,
        "height": 50.0,
        "rotation": 45.0,
        "text": "Hello\\nWorld",
        "metadata": {"lines": ["Hello", "World"]}
    }
    item = AnnotationItem(**data)
    assert item.width == 100.0
    assert item.height == 50.0
    assert item.rotation == 45.0
    
    # Legacy text
    data_legacy = {
        "id": "legacy_1",
        "type": "text",
        "x": 10.0,
        "y": 10.0,
        "text": "Legacy"
    }
    legacy_item = AnnotationItem(**data_legacy)
    assert getattr(legacy_item, "width", None) is None

def test_svg_export_escaped_and_rotated():
    img = Image.new("RGB", (100, 100), color=(0, 0, 0))
    # Test XML escaping and rotation
    item = AnnotationItem(
        type="text",
        x=10.0,
        y=10.0,
        width=50.0,
        height=20.0,
        rotation=30.0,
        text="<Dangerous> & 'Text'",
        metadata={"lines": ["<Dangerous>", "& 'Text'"]}
    )
    
    scene = SceneConfig()
    annot_set = AnnotationSet(items=[item])
    
    svg_content = generate_svg_export(
        base_img=img,
        annotations=annot_set,
        measurements=None,
        metadata={},
        scene=scene
    )
    
    # Check XML escaping
    assert "&lt;Dangerous&gt;" in svg_content
    assert "&amp; &#x27;Text&#x27;" in svg_content
    
    # Check rotation (approx cx=10+25=35, cy=10+20/2=20 or similar depending on actual height max)
    # text_total_height = 2 lines * 15 = 30. box_height = max(20, 30) = 30
    # cy = 10 + 30/2 = 25
    assert "transform=\"rotate(30.0 35.0 25.0)\"" in svg_content
    
    # Legacy text should not have transform
    legacy = AnnotationItem(type="text", x=10.0, y=10.0, text="Legacy")
    svg_content_legacy = generate_svg_export(
        base_img=img,
        annotations=AnnotationSet(items=[legacy]),
        measurements=None,
        metadata={},
        scene=scene
    )
    assert "transform=\"rotate" not in svg_content_legacy
    assert "Legacy" in svg_content_legacy
