"""Descompressor LZO1X (formato usado pela Ampero II nos dumps de patch).
Segue o lzo1x_decompress de referência (minilzo)."""


def lzo1x_decompress(src: bytes) -> bytes:
    out = bytearray()
    ip = 0

    def zero_ext(base):
        nonlocal ip
        t = 0
        while src[ip] == 0:
            t += 255
            ip += 1
        t += base + src[ip]
        ip += 1
        return t

    def lits(n):
        nonlocal ip
        out.extend(src[ip:ip + n])
        ip += n

    def copy(dist_back, n):
        # dist_back: quantos bytes para trás a partir do fim de out
        s = len(out) - dist_back
        if s < 0:
            raise ValueError("match antes do início")
        for i in range(n):
            out.append(out[s + i])

    state = "loop"
    t = src[0]
    if t > 17:
        ip = 1
        t -= 17
        if t < 4:
            state = "match_next"
        else:
            lits(t)
            state = "first_literal_run"

    while True:
        if state == "loop":
            t = src[ip]; ip += 1
            if t >= 16:
                state = "match"
                continue
            if t == 0:
                t = zero_ext(15)
            lits(t + 3)
            state = "first_literal_run"
        elif state == "first_literal_run":
            t = src[ip]; ip += 1
            if t >= 16:
                state = "match"
                continue
            back = 1 + 0x0800 + (t >> 2) + (src[ip] << 2); ip += 1
            copy(back, 3)
            state = "match_done"
        elif state == "match":
            if t >= 64:
                back = 1 + ((t >> 2) & 7) + (src[ip] << 3); ip += 1
                copy(back, (t >> 5) - 1 + 2)
            elif t >= 32:
                n = t & 31
                if n == 0:
                    n = zero_ext(31)
                back = 1 + ((src[ip] | (src[ip + 1] << 8)) >> 2); ip += 2
                copy(back, n + 2)
            elif t >= 16:
                back = (t & 8) << 11
                n = t & 7
                if n == 0:
                    n = zero_ext(7)
                back += (src[ip] | (src[ip + 1] << 8)) >> 2; ip += 2
                if back == 0:
                    return bytes(out)  # fim do fluxo
                back += 0x4000
                copy(back, n + 2)
            else:
                back = 1 + (t >> 2) + (src[ip] << 2); ip += 1
                copy(back, 2)
            state = "match_done"
        elif state == "match_done":
            t = src[ip - 2] & 3
            state = "loop" if t == 0 else "match_next"
        elif state == "match_next":
            lits(t)
            t = src[ip]; ip += 1
            state = "match"
