# Public face fixture, not household imagery

`astronaut.png` is the scikit-image astronaut portrait, originally downloaded from
NASA's Great Images database. Scikit-image documents it as released into the public
domain with no known copyright restrictions:
https://scikit-image.org/docs/stable/api/skimage.data.html#skimage.data.astronaut

Downloaded unmodified from scikit-image release v0.25.2 (tag commit
`23308914c880574fe7c716e2ce9aa698ab0d69b4`):
https://raw.githubusercontent.com/scikit-image/scikit-image/v0.25.2/skimage/data/astronaut.png

SHA-256: `88431cd9653ccd539741b555fb0a46b61558b301d4110412b5bc28b5e3ea6cb5`
Dimensions: 512x512 RGB. No household, patient or user image is used in these tests.
The hand-annotated central facial extent for the regression is x=175..270,
y=65..170. It is independent of the detector's expanded bounding box.

The two-face test composites two copies in memory. Generated blank color images
and metadata strings are synthetic. Tests do not write unredacted copies of image
inputs inside the detector subprocess or send the fixture to any image service.
