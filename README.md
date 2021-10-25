# BlenderNoriPlugin
Export blender scenes to the [Nori educational raytracer](https://github.com/wjakob/nori). Proposed and used by many in the [Computer Graphics course at ETH Zurich, Fall 2020](https://cgl.ethz.ch/teaching/cg20/home.php).

This is an extension and partial reimplementation to the [official Nori plugin](https://github.com/wjakob/nori/tree/master/ext/plugin) for Blender 2.80+ (tested on 2.90).

## Installation
To use it, copy the file 'io_nori.py' into the addons/ folder of your blender installation. Then, go to Edit -> Preferences -> Add-ons -> Testing, and enable "Export Nori scenes format.". Then, you can export your scene from File -> Export -> Export Nori scenes... .

## Notes

- Only "visible" objects are exported, so by switching the eye icon of your objects on/off you can decide what to export.
- Object instances are also supported. The exporter silently unrolls the instances into a single mesh, exports it and sets it back to the state before.
- By enabling the option "Export lights", the exporter will export : Point Lights as pointlights and Objects with a "Emission" BSDF as area lights. Thus, you can export mesh area - lights to Nori. Note: For now Blender Area Lights are not exported (they only support simple geometry). When "Export lights" is selected, the default integrator is "path_mis", otherwise "normals".
- By enabling the option "Export BSDF properties", the exporter will export the mesh and add a translated BSDF entry to the xml file for this object (multiple materials for different faces of a single object are also supported). Otherwise, a diffuse BSDF with the viewport color of the object is added to the XML. The following translations are currently available:
    Principled BSDF -> disney
    Diffuse BSDF -> diffuse
    Specular -> mirror
    Glass BSDF -> dielectric (also exports values for rough dielectric, so change the name in the xml afterwards if you have implemented this)
    Glossy BSDF -> Microfacet
All other materials will be exported as diffuse. If you have some kind of hierarchical materials (mix shader etc), the exporter will do some heuristics to decide what to export, so change it afterwards in the XML.
- By enabling the option "Export textures", the exporter adds a texture to your BSDF entry if available. Note that only image textures are supported atm. If no texture is found, the default BSDF color is exported. To use this feature, add an Image Texture Node and connect it to the "Color" or "Base Color" socket of your object. The path to the image will be copied relative to the blender file for now, so if you want to use the output right away save the XML in the same folder as the blender file.
- The checkbox "Export OBJ in world coords" defines how your obj files are exported: If enabled, we export the mesh in world coordinates of Blender. Otherwise, we export the mesh in local coordinates and add a toWorld transform to the XML entry.
- The checkbox "Triangular Mesh" exports all your meshes as triangular meshes. This is helpful if your mesh has complex polygons that Nori does not support. Note that after this export your mesh in Blender stays triangular.
- The "Number of Camera Rays" input box sets the sampleCount parameter of your sampler.

## Disclaimer
Note that the Plugin is in an early stage and thus might have its problems/limitations. I would be happy to hear your feedback, so we could improve this together. I hope it helps someone in their final projects!
