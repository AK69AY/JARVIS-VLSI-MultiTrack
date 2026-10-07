`timescale 1ns/1ps

module tb_unified_suite;

    // ---------------- BitNet PE Signals ----------------
    reg clk, rst_n, enable;
    reg [7:0] in_data;
    reg [1:0] in_weight;
    reg [15:0] acc_in;
    wire [15:0] acc_out;

    bitnet_ternary_pe pe_inst (
        .clk(clk), .rst_n(rst_n), .enable(enable),
        .in_data(in_data), .in_weight(in_weight),
        .acc_in(acc_in), .acc_out(acc_out)
    );

    // ---------------- Async FIFO Signals ----------------
    reg  wclk, wrst_n, winc;
    reg  [7:0] wdata;
    wire wfull;
    reg  rclk, rrst_n, rinc;
    wire [7:0] rdata;
    wire rempty;

    async_fifo_cdc #(.DATA_WIDTH(8), .ADDR_WIDTH(4)) fifo_inst (
        .wclk(wclk), .wrst_n(wrst_n), .winc(winc), .wdata(wdata), .wfull(wfull),
        .rclk(rclk), .rrst_n(rrst_n), .rinc(rinc), .rdata(rdata), .rempty(rempty)
    );

    // ---------------- Clock Generation ----------------
    always #5 clk = ~clk;       // 100 MHz PE clock
    always #4 wclk = ~wclk;     // 125 MHz write domain
    always #7 rclk = ~rclk;     // ~71 MHz read domain (async to wclk)

    integer errors;
    integer i;

    // ---------------- PE Checking Task ----------------
    task pe_op;
        input [7:0]  data;
        input [1:0]  weight;
        input [15:0] acc;
        input signed [15:0] expected;
        begin
            @(negedge clk);
            in_data = data; in_weight = weight; acc_in = acc;
            @(posedge clk);
            #1;
            if ($signed(acc_out) !== expected) begin
                $display("[FAIL] PE: data=%0d w=%b acc_in=%0d -> got %0d, expected %0d",
                          $signed(data), weight, $signed(acc), $signed(acc_out), expected);
                errors = errors + 1;
            end
        end
    endtask

    initial begin
        $dumpfile("sim_output.vcd");
        $dumpvars(0, tb_unified_suite);

        errors = 0;
        clk = 0; rst_n = 0; enable = 0;
        wclk = 0; rclk = 0; wrst_n = 0; rrst_n = 0;
        in_data = 0; in_weight = 2'b00; acc_in = 0;
        winc = 0; wdata = 0; rinc = 0;

        #12 rst_n = 1; enable = 1;
        wrst_n = 1; rrst_n = 1;

        // ===== Track 2: Ternary PE verification =====
        pe_op(8'd12,  2'b01, 16'd0,   16'sd12);    // 0 + (+12)
        pe_op(8'd12,  2'b10, 16'd12,  16'sd0);     // 12 + (-12)
        pe_op(8'd12,  2'b01, 16'd0,   16'sd12);    // 0 + (+12)
        pe_op(8'd25,  2'b01, 16'd12,  16'sd37);    // 12 + 25
        pe_op(8'd25,  2'b10, 16'd37,  16'sd12);    // 37 - 25
        pe_op(8'd25,  2'b00, 16'd12,  16'sd12);    // weight 0 -> unchanged
        pe_op(8'd100, 2'b10, 16'd0,   -16'sd100);  // negative accumulation
        pe_op(8'd1,   2'b01, -16'sd100, -16'sd99); // -100 + 1

        // Reset behavior
        @(negedge clk); rst_n = 0;
        @(negedge clk); rst_n = 1;
        #1;
        if (acc_out !== 16'd0) begin
            $display("[FAIL] PE reset: acc_out=%0d, expected 0", acc_out);
            errors = errors + 1;
        end

        // ===== Track 3: Async FIFO write-then-read across CDC =====
        // Fill FIFO completely (DEPTH = 16)
        for (i = 0; i < 16; i = i + 1) begin
            @(negedge wclk);
            winc = 1; wdata = i + 8'hA0;
        end
        @(negedge wclk); winc = 0;

        // Allow flags to settle; FIFO must now be full
        repeat (4) @(posedge wclk);
        if (wfull !== 1'b1) begin
            $display("[FAIL] FIFO not full after 16 writes (wfull=%b)", wfull);
            errors = errors + 1;
        end

        // Overflow attempt must be blocked
        @(negedge wclk); winc = 1; wdata = 8'hFF;
        @(negedge wclk); winc = 0;
        repeat (2) @(posedge wclk);

        // Drain and verify data integrity across clock domains
        for (i = 0; i < 16; i = i + 1) begin
            @(negedge rclk);
            if (rempty !== 1'b0) begin
                $display("[FAIL] FIFO unexpectedly empty at read %0d", i);
                errors = errors + 1;
            end
            if (rdata !== (i + 8'hA0)) begin
                $display("[FAIL] FIFO data mismatch at %0d: got %02h, expected %02h",
                         i, rdata, i + 8'hA0);
                errors = errors + 1;
            end
            rinc = 1;
        end
        @(negedge rclk); rinc = 0;

        // Empty flag must assert after full drain
        repeat (4) @(posedge rclk);
        if (rempty !== 1'b1) begin
            $display("[FAIL] FIFO not empty after full drain (rempty=%b)", rempty);
            errors = errors + 1;
        end

        #20;
        if (errors == 0)
            $display("[VERIFICATION PASSED] All checks passed. PE ops + FIFO CDC data integrity OK.");
        else
            $display("[VERIFICATION FAILED] %0d error(s).", errors);
        $finish;
    end

endmodule
