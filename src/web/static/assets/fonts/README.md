# Local panel font

Noto Sans SC variable font is bundled as `NotoSansSC-VF.woff2`, under the SIL Open Font License in `OFL.txt`. It covers the full installed font's character set; no UI-text-only subsetting is used, so newly collected Chinese text does not depend on the current interface copy. The original 17,773,244-byte TTF is losslessly encoded as a 7,783,292-byte WOFF2 with fontTools 4.67.0; glyphs and variable weights are preserved. fontTools is a development-only tool and is not a product dependency.

The source binary is the Noto Sans SC font already installed on the development machine. Official project: https://github.com/notofonts/noto-cjk . License reference: https://github.com/google/fonts/blob/main/ofl/notosanssc/OFL.txt . CSS uses the family alias `Periscope Sans` with a 100–900 weight range, and serves it from the local `/assets/fonts/` path. No runtime font CDN is needed.
