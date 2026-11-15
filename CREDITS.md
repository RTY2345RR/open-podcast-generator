# Credits

## Demo voices: CMU ARCTIC

The default hosts are cloned from one reference clip each from the CMU ARCTIC speech corpus
(http://www.festvox.org/cmu_arctic/), Carnegie Mellon University:

- **Alex** (male): speaker `bdl`, `cmu_us_bdl_arctic/wav/arctic_a0003.wav`
- **Sam** (female): speaker `slt`, `cmu_us_slt_arctic/wav/arctic_a0003.wav`

Reference text: "For the twentieth time that evening the two men shook hands."
The clips are fetched from festvox.org at render time; they are not redistributed in this repository.
The audio in `examples/` was generated with voices cloned from these clips. Licence notice:

```

This voice is free for use for any purpose (commercial or otherwise)
subject to the pretty light restrictions detailed below.

############################################################################
###                                                                       ##
###                     Carnegie Mellon University                        ##
###                         Copyright (c) 2003                            ##
###                        All Rights Reserved.                           ##
###                                                                       ##
###  Permission to use, copy, modify,  and licence this software and its  ##
###  documentation for any purpose, is hereby granted without fee,        ##
###  subject to the following conditions:                                 ##
###   1. The code must retain the above copyright notice, this list of    ##
###      conditions and the following disclaimer.                         ##
###   2. Any modifications must be clearly marked as such.                ##
###   3. Original authors' names are not deleted.                         ##
###                                                                       ##
###  THE AUTHORS OF THIS WORK DISCLAIM ALL WARRANTIES WITH REGARD TO      ##
###  THIS SOFTWARE, INCLUDING ALL IMPLIED WARRANTIES OF MERCHANTABILITY   ##
###  AND FITNESS, IN NO EVENT SHALL THE AUTHORS BE LIABLE FOR ANY         ##
###  SPECIAL, INDIRECT OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES            ##
###  WHATSOEVER RESULTING FROM LOSS OF USE, DATA OR PROFITS, WHETHER IN   ##
###  AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER TORTIOUS ACTION,          ##
###  ARISING OUT OF OR IN CONNECTION WITH THE USE OR PERFORMANCE OF       ##
###  THIS SOFTWARE.                                                       ##
###                                                                       ##
############################################################################
###                                                                       ##
###  See http://www.festvox.org/cmu_arctic/ for more details              ##
###                                                                       ##
############################################################################

```

## Voice engine

Speech is rendered by the Miragea voice-cloning engine (https://miragea.ai).

## Libraries

httpx (BSD-3-Clause), pypdf (BSD-3-Clause), imageio-ffmpeg (BSD-2-Clause, bundles FFmpeg under LGPL/GPL),
optional yt-dlp (Unlicense). No code was copied from other podcast generators.
