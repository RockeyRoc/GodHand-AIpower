#pragma once
#include <stdlib.h>
#include <stdio.h>
#include <string.h>
static char *aoi_json_escape(const char *input) {
    char *output = calloc(strlen(input) * 6 + 1, 1), *p = output;
    if (!output) exit(1);
    for (const unsigned char *s = (const unsigned char *)input; *s; s++) {
        if (*s < 32) { sprintf(p, "\\u%04x", *s); p += 6; }
        else { if (*s == '"' || *s == '\\') *p++ = '\\'; *p++ = *s; }
    }
    *p = 0;
    return output;
}
