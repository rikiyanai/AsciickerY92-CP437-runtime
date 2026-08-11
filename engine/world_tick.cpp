// world_tick.cpp — Per-tick world advancement (sprite animation)
//
// Extracted from engine/world.cpp.
// Contains: AnimateSpriteInst — advances sprite animation frames based on
// wall-clock stamp and per-instance repetition configuration.
//
// SEE ALSO: world.h, world_internal.h

#include "world_internal.h"

// ============================================================================
// AnimateSpriteInst
// ============================================================================

int AnimateSpriteInst(Inst* i, uint64_t stamp)
{
    if (!i || i->inst_type != Inst::SPRITE)
        return -1;

    SpriteInst* si = (SpriteInst*)i;
    Sprite* sp = si->sprite;
    if (!sp || sp->anims <= 0)
        return 0;

    int anim = si->anim;
    if (anim < 0 || anim >= sp->anims)
        anim = 0;
    int anim_len = sp->anim[anim].length;
    if (anim_len <= 0)
        return 0;

    int time = 0;
    int len = si->reps[0] + si->reps[1] * anim_len + si->reps[2] + si->reps[3] * anim_len;

    int frame = 0;
    if (len <= 0)
    {
        frame = si->frame % anim_len;
    }
    else
    {
        time = (stamp >> 14) % len; // ~61 FPS
        int hold_start = si->reps[0];
        int forward_end = hold_start + si->reps[1] * anim_len;
        int reverse_start = forward_end + si->reps[2];

        if (time < hold_start)
            frame = 0;
        else if (si->reps[1] > 0 && time < forward_end)
            frame = (time - si->reps[0]) / si->reps[1];
        else if (time < reverse_start)
            frame = anim_len - 1;
        else if (si->reps[3] > 0)
            frame = anim_len - 1 - (time - reverse_start) / si->reps[3];
        else
            frame = anim_len - 1;
    }

    return frame;
}
